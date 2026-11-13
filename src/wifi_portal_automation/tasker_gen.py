"""Generate Tasker ``.prj.xml`` from Python data structures.

The hand-edited XML in ``android/MVwifiAuto.prj.xml`` was fragile:
parameter-mapping bugs (Sessions 14, 17 of the devlog) were caused by
typos in raw XML that no tool could catch.  This module replaces that
process with testable Python data structures and ElementTree-based
generation.

Usage::

    from wifi_portal_automation.tasker_gen import build_mvwifi_project, generate_project_xml

    project = build_mvwifi_project()
    xml_text = generate_project_xml(project)
    Path("android/MVwifiAuto.prj.xml").write_text(xml_text)

Or via the CLI::

    python -m wifi_portal_automation.tasker_gen --output android/MVwifiAuto.prj.xml
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass, field, replace
from xml.etree.ElementTree import (
    Element,
    SubElement,
    fromstring,
    indent,
    tostring,
)

# ---------------------------------------------------------------------------
# Action codes. Reference table (verify against this before adding or
# changing any code — a wrong constant imports silently as a different
# action; e.g. 731 was once used here for Goto and rendered as Take Call):
# https://github.com/Taskomater/Tasker-XML-Info/blob/master/Tasker_XML_Codes.md
# ---------------------------------------------------------------------------
CODE_IF = 37
CODE_END_IF = 38
CODE_ELSE = 43
CODE_WAIT = 30
CODE_STOP = 137
CODE_PERFORM_TASK = 130
CODE_RUN_SHELL = 123
CODE_VARIABLE_SET = 547
CODE_FLASH = 548
CODE_VARIABLE_SPLIT = 590
CODE_VARIABLE_SEARCH_REPLACE = 598
CODE_HTTP_REQUEST = 339
CODE_CONNECT_WIFI = 398
CODE_WRITE_FILE = 410
CODE_NOTIFY = 523
CODE_WIFI_NEAR_STATE = 170
CODE_WIFI_CONNECTED_STATE = 160

# Profile event codes (serialized as <Event>)
CODE_DISPLAY_UNLOCKED_EVENT = 1000

# Termux:Tasker plugin action code (from official Termux:Tasker template export)
CODE_TERMUX_TASK = 1256900802

# Goto action code (135 = Goto; 731 is Take Call — do not confuse)
CODE_GOTO = 135

# Condition operator: 2 = equals (~ in Tasker UI)
OP_EQUALS = 2

# Tasker version string for the root element
DEFAULT_TASKER_VERSION = "5.15.14"

# Generation stamp placeholder.  ``generate_project_xml`` replaces every
# occurrence with a content hash of the generated XML (with the
# placeholder still in place), so the stamp tracks the exact project
# content with no manual version bumping.  Embedded in task marker text
# and the ``MVW_ShowVersion`` flash, it lets logs and the Tasker GUI answer
# "which generation is actually imported?" — a stale merged import is
# instantly visible as an old/absent gen id.
GEN_PLACEHOLDER = "@MWGEN@"

# Fixed bundle for HTTP Request (code 339) — Tasker requires this metadata
_HTTP_BUNDLE_XML = (
    '<Vals sr="val">'
    "<net.dinglisch.android.tasker.RELEVANT_VARIABLES>"
    '<StringArray sr=""></StringArray>'
    "</net.dinglisch.android.tasker.RELEVANT_VARIABLES>"
    "<net.dinglisch.android.tasker.RELEVANT_VARIABLES-type>"
    "[Ljava.lang.String;"
    "</net.dinglisch.android.tasker.RELEVANT_VARIABLES-type>"
    "</Vals>"
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class TaskerArg:
    """A single action argument (Str, Int, or Bundle)."""

    index: int
    kind: str  # "Str", "Int", or "Bundle"
    value: str = ""
    ve: str = "3"  # version attribute for Str args

    @classmethod
    def str_arg(cls, index: int, value: str = "", ve: str = "3") -> TaskerArg:
        """Create a string argument."""
        return cls(index=index, kind="Str", value=value, ve=ve)

    @classmethod
    def int_arg(cls, index: int, value: int = 0) -> TaskerArg:
        """Create an integer argument."""
        return cls(index=index, kind="Int", value=str(value))

    @classmethod
    def bundle_arg(cls, index: int, xml_content: str = "") -> TaskerArg:
        """Create a bundle argument (for HTTP Request metadata)."""
        return cls(index=index, kind="Bundle", value=xml_content)

    @classmethod
    def img_arg(cls, index: int, ve: str = "2") -> TaskerArg:
        """Create an empty image/icon argument."""
        return cls(index=index, kind="Img", ve=ve)


@dataclass
class TaskerCondition:
    """An If-block condition."""

    lhs: str
    op: int
    rhs: str


@dataclass
class TaskerAction:
    """A Tasker action within a task.

    ``label`` is the free-text label shown next to the action in the
    Tasker UI — the closest thing Tasker has to an inline comment.
    Use it on opaque actions (plugin calls, regexes, markers) so the
    task reads clearly when browsed inside Tasker.
    """

    code: int
    args: list[TaskerArg] = field(default_factory=list)
    conditions: list[TaskerCondition] | None = None
    continue_on_error: bool = False
    label: str = ""


@dataclass
class TaskerTask:
    """A Tasker task."""

    id: int
    name: str
    priority: int = 2
    actions: list[TaskerAction] = field(default_factory=list)


@dataclass
class TaskerState:
    """A profile state condition (e.g. WiFi Near)."""

    code: int
    args: list[TaskerArg]


@dataclass
class TaskerEvent:
    """A profile event condition (e.g. Display Unlocked).

    Serializes as ``<Event>`` — unlike a State, an event has no
    active/inactive lifecycle; it fires once per occurrence.
    """

    code: int
    args: list[TaskerArg] = field(default_factory=list)


@dataclass
class TaskerTime:
    """A profile time context (serializes as ``<Time>``).

    ``from_*``/``to_*`` of -1 means unset (whole day).  When
    ``repeat_unit`` is non-zero the context fires as an instant event
    every ``repeat_value`` units between From and To.  Repeat units in
    Tasker exports: 1 = hours, 2 = minutes.
    """

    from_hour: int = -1
    from_minute: int = -1
    to_hour: int = -1
    to_minute: int = -1
    repeat_unit: int = 0
    repeat_value: int = 0


@dataclass
class TaskerProfile:
    """A Tasker profile linking a context trigger to a task."""

    id: int
    name: str
    state: TaskerState | TaskerTime | TaskerEvent
    task_id: int


@dataclass
class TaskerProject:
    """A complete Tasker project (root container for import)."""

    name: str
    profiles: list[TaskerProfile]
    tasks: list[TaskerTask]
    tasker_version: str = DEFAULT_TASKER_VERSION


# ---------------------------------------------------------------------------
# XML generation
# ---------------------------------------------------------------------------
def _add_arg(parent: Element, arg: TaskerArg) -> None:
    """Add an argument element to *parent*."""
    if arg.kind == "Str":
        elem = SubElement(
            parent,
            "Str",
            {"sr": f"arg{arg.index}", "ve": arg.ve},
        )
        elem.text = arg.value
    elif arg.kind == "Int":
        SubElement(
            parent,
            "Int",
            {"sr": f"arg{arg.index}", "val": arg.value},
        )
    elif arg.kind == "Img":
        SubElement(parent, "Img", {"sr": f"arg{arg.index}", "ve": arg.ve})
    elif arg.kind == "Bundle":
        bundle = SubElement(parent, "Bundle", {"sr": f"arg{arg.index}"})
        # Parse the bundle XML string and insert it as child elements.
        # If we set it as .text, the XML special characters get escaped
        # (&lt; instead of <), which Tasker cannot parse as nested bundle data.
        try:
            bundle_root = fromstring(arg.value)
            bundle.append(bundle_root)
        except Exception:
            # If for some reason it's not valid XML, keep the original text
            bundle.text = arg.value


def _add_action(task_elem: Element, index: int, action: TaskerAction) -> None:
    """Add an action element to *task_elem*."""
    act = SubElement(task_elem, "Action", {"sr": f"act{index}", "ve": "7"})
    if action.continue_on_error:
        # Tasker's "Continue Task After Error" serializes as <se>false</se>
        # (stop-on-error=false), verified against Tasker's own persisted
        # export format.  An unrecognized element (e.g. <con>) is silently
        # dropped on import — the action then aborts the task on error.
        SubElement(act, "se").text = "false"
    SubElement(act, "code").text = str(action.code)
    if action.label:
        SubElement(act, "label").text = action.label

    if action.conditions:
        cond_list = SubElement(act, "ConditionList", {"sr": "if"})
        for ci, cond in enumerate(action.conditions):
            cond_elem = SubElement(cond_list, "Condition", {"sr": f"c{ci}", "ve": "3"})
            SubElement(cond_elem, "lhs").text = cond.lhs
            SubElement(cond_elem, "op").text = str(cond.op)
            SubElement(cond_elem, "rhs").text = cond.rhs

    for arg in action.args:
        _add_arg(act, arg)


def _add_task(root: Element, task: TaskerTask) -> None:
    """Add a task element to *root*."""
    task_elem = SubElement(root, "Task", {"sr": f"task{task.id}"})
    SubElement(task_elem, "id").text = str(task.id)
    SubElement(task_elem, "nme").text = task.name
    SubElement(task_elem, "rty").text = str(task.priority)
    for i, action in enumerate(task.actions):
        _add_action(task_elem, i, action)


def _add_profile(root: Element, profile: TaskerProfile) -> None:
    """Add a profile element to *root*."""
    prof = SubElement(root, "Profile", {"sr": f"prof{profile.id}", "ve": "2"})
    SubElement(prof, "cdate").text = "1000000000000"
    SubElement(prof, "edate").text = "1000000000000"
    SubElement(prof, "id").text = str(profile.id)
    SubElement(prof, "mid0").text = str(profile.task_id)
    SubElement(prof, "nme").text = profile.name

    if isinstance(profile.state, TaskerTime):
        ctx = SubElement(prof, "Time", {"sr": "con0", "ve": "2"})
        SubElement(ctx, "fh").text = str(profile.state.from_hour)
        SubElement(ctx, "fm").text = str(profile.state.from_minute)
        if profile.state.repeat_unit:
            SubElement(ctx, "rep").text = str(profile.state.repeat_unit)
            SubElement(ctx, "repval").text = str(profile.state.repeat_value)
        SubElement(ctx, "th").text = str(profile.state.to_hour)
        SubElement(ctx, "tm").text = str(profile.state.to_minute)
    elif isinstance(profile.state, TaskerEvent):
        ctx = SubElement(prof, "Event", {"sr": "con0", "ve": "2"})
        SubElement(ctx, "code").text = str(profile.state.code)
        for arg in profile.state.args:
            _add_arg(ctx, arg)
    else:
        state = SubElement(prof, "State", {"sr": "con0", "ve": "2"})
        SubElement(state, "code").text = str(profile.state.code)
        for arg in profile.state.args:
            _add_arg(state, arg)


def _add_project(root: Element, project: TaskerProject) -> None:
    """Add the project element to *root*."""
    proj = SubElement(root, "Project", {"sr": "proj0", "ve": "2"})
    SubElement(proj, "name").text = project.name
    SubElement(proj, "pids").text = ",".join(str(p.id) for p in project.profiles)
    SubElement(proj, "tids").text = ",".join(str(t.id) for t in project.tasks)


def generate_project_xml(project: TaskerProject) -> str:
    """Generate the Tasker ``.prj.xml`` file content.

    Args:
        project: The project to serialize.

    Returns:
        XML string suitable for writing to a ``.prj.xml`` file.
    """
    root = Element(
        "TaskerData",
        {"sr": "", "dvi": "1", "tv": project.tasker_version},
    )

    for profile in project.profiles:
        _add_profile(root, profile)

    _add_project(root, project)

    for task in project.tasks:
        _add_task(root, task)

    # Pretty-print with tab indentation (matching Tasker's export format)
    indent(root, space="\t")

    xml_bytes = tostring(root, encoding="unicode")
    xml_text = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_bytes + "\n"

    # Stamp the generation id: hash the serialized XML *with* the
    # placeholder in place (so the stamp can't change its own hash),
    # then substitute it everywhere.  Any change to tasks, profiles,
    # or arguments produces a new gen id automatically.
    gen = hashlib.sha256(xml_text.encode()).hexdigest()[:6]
    return xml_text.replace(GEN_PLACEHOLDER, gen)


# ---------------------------------------------------------------------------
# Action builder helpers
def _lbl(action: TaskerAction, label: str) -> TaskerAction:
    """Return a copy of *action* with a Tasker UI label attached.

    Tasker has no real comment syntax; an action label is the closest
    equivalent — free text shown next to the action in the editor and
    in XML exports (``<label>`` child of ``<Action>``).
    """
    return replace(action, label=label)
# ---------------------------------------------------------------------------
def perform_task(
    name: str,
    par1: str = "",
    stop_after: bool = False,
    wait_for_finish: bool = True,
    priority: int = 0,
) -> TaskerAction:
    """Build a Perform Task (code 130) action."""
    return TaskerAction(
        code=CODE_PERFORM_TASK,
        args=[
            TaskerArg.str_arg(0, name),
            TaskerArg.int_arg(1, priority),
            TaskerArg.str_arg(2, par1),
            TaskerArg.str_arg(3),
            TaskerArg.str_arg(4),
            TaskerArg.str_arg(5),
            TaskerArg.int_arg(6, 1 if stop_after else 0),
            TaskerArg.str_arg(7),
            TaskerArg.int_arg(8, 0),
            TaskerArg.int_arg(9, 0),
            TaskerArg.int_arg(10, 1 if wait_for_finish else 0),
        ],
    )


def flash(text: str) -> TaskerAction:
    """Build a Flash (code 548) action."""
    return TaskerAction(
        code=CODE_FLASH,
        args=[
            TaskerArg.str_arg(0, text),
            TaskerArg.int_arg(1, 0),
        ],
    )


def notify(title: str, text: str) -> TaskerAction:
    """Build a Notify (code 523) action — persistent shade entry.

    Arg layout mirrors a captured on-device export: title/text Strs,
    empty Img icon slot, Int flags (arg5=5 = medium priority), and
    trailing Str fields.
    """
    return TaskerAction(
        code=CODE_NOTIFY,
        args=[
            TaskerArg.str_arg(0, title),
            TaskerArg.str_arg(1, text),
            TaskerArg.img_arg(2),
            TaskerArg.int_arg(3, 0),
            TaskerArg.int_arg(4, 0),
            TaskerArg.int_arg(5, 5),
            TaskerArg.int_arg(6, 0),
            TaskerArg.int_arg(7, 0),
            TaskerArg.int_arg(8, 0),
            TaskerArg.str_arg(10),
            TaskerArg.str_arg(11, "0"),
            TaskerArg.str_arg(12),
            TaskerArg.int_arg(13, 0),
            TaskerArg.str_arg(14),
            TaskerArg.str_arg(15),
        ],
    )


def variable_set(name: str, value: str) -> TaskerAction:
    """Build a Variable Set (code 547) action."""
    return TaskerAction(
        code=CODE_VARIABLE_SET,
        args=[
            TaskerArg.str_arg(0, name),
            TaskerArg.str_arg(1, value),
            TaskerArg.int_arg(2, 0),
            TaskerArg.int_arg(3, 0),
        ],
    )


def if_condition(lhs: str, rhs: str, op: int = OP_EQUALS) -> TaskerAction:
    """Build an If (code 37) action with a single condition."""
    return TaskerAction(
        code=CODE_IF,
        conditions=[TaskerCondition(lhs=lhs, op=op, rhs=rhs)],
    )


def end_if() -> TaskerAction:
    """Build an End If (code 38) action."""
    return TaskerAction(code=CODE_END_IF)


def else_action() -> TaskerAction:
    """Build an Else (code 43) action."""
    return TaskerAction(code=CODE_ELSE)


def wait(seconds: int = 0, minutes: int = 0) -> TaskerAction:
    """Build a Wait (code 30) action."""
    return TaskerAction(
        code=CODE_WAIT,
        args=[
            TaskerArg.int_arg(0, 0),  # ms
            TaskerArg.int_arg(1, seconds),
            TaskerArg.int_arg(2, minutes),
            TaskerArg.int_arg(3, 0),  # hours
            TaskerArg.int_arg(4, 0),
        ],
    )


def write_file(
    path: str, text: str, append: bool = True, add_newline: bool = True
) -> TaskerAction:
    """Build a Write File (code 410) action.

    Args:
        path: File path relative to /sdcard (Tasker resolves it).
        text: Content to write (Tasker variables like %TIMES work).
        append: Append to the file instead of overwriting.
        add_newline: Append a newline after the content.
    """
    return TaskerAction(
        code=CODE_WRITE_FILE,
        args=[
            TaskerArg.str_arg(0, path),
            TaskerArg.str_arg(1, text),
            TaskerArg.int_arg(2, 1 if append else 0),  # Append
            TaskerArg.int_arg(3, 1 if add_newline else 0),  # Add Newline
            TaskerArg.int_arg(4, 0),  # emitted by newer Tasker versions
        ],
    )


def stop() -> TaskerAction:
    """Build a Stop (code 137) action."""
    return TaskerAction(code=CODE_STOP)


def variable_search_replace(
    var_name: str,
    search: str,
    result_var: str = "",
) -> TaskerAction:
    """Build a Variable Search Replace (code 598) action."""
    return TaskerAction(
        code=CODE_VARIABLE_SEARCH_REPLACE,
        args=[
            TaskerArg.str_arg(0, var_name),
            TaskerArg.str_arg(1, search),
            TaskerArg.str_arg(2, result_var),
            TaskerArg.int_arg(3, 0),
            TaskerArg.int_arg(4, 0),
        ],
    )


def http_request(
    method: str = "GET",
    url: str = "",
    headers: str = "",
    body: str = "",
    timeout: int = 10,
    follow_redirects: bool = True,
) -> TaskerAction:
    """Build an HTTP Request (code 339) action.

    Args:
        method: "GET" or "POST".
        url: Request URL (Tasker variables expanded at runtime).
        headers: Newline-separated "Key:Value" header lines.
        body: POST body (URL-encoded).
        timeout: Timeout in seconds.
        follow_redirects: Whether to follow HTTP redirects.
    """
    method_code = 1 if method.upper() == "POST" else 0
    redirects_code = 0 if follow_redirects else 1
    return TaskerAction(
        code=CODE_HTTP_REQUEST,
        args=[
            TaskerArg.bundle_arg(0, _HTTP_BUNDLE_XML),
            TaskerArg.int_arg(1, method_code),
            TaskerArg.str_arg(2, url),
            TaskerArg.str_arg(3, headers),
            TaskerArg.str_arg(4, body),
            TaskerArg.str_arg(5),
            TaskerArg.str_arg(6),
            TaskerArg.str_arg(7),
            TaskerArg.int_arg(8, timeout),
            TaskerArg.int_arg(9, redirects_code),
            TaskerArg.int_arg(10, 0),
            TaskerArg.int_arg(11, 0),
            TaskerArg.int_arg(12, 1),
        ],
    )


def connect_wifi(ssid: str, continue_on_error: bool = False) -> TaskerAction:
    """Build a Connect to WiFi (code 398) action."""
    return TaskerAction(
        code=CODE_CONNECT_WIFI,
        continue_on_error=continue_on_error,
        args=[
            TaskerArg.str_arg(0, ssid),
            TaskerArg.int_arg(1, 0),
            TaskerArg.str_arg(2),
            TaskerArg.int_arg(3, 0),
            TaskerArg.int_arg(4, 0),
        ],
    )


def run_shell(
    command: str,
    timeout: int = 10,
    use_root: bool = False,
    output_var: str = "",
    continue_on_error: bool = False,
) -> TaskerAction:
    """Build a Run Shell (code 123) action.

    Note: arg1=timeout, arg2=use_root, arg3=output_var (Tasker 6.x order).
    """
    return TaskerAction(
        code=CODE_RUN_SHELL,
        continue_on_error=continue_on_error,
        args=[
            TaskerArg.str_arg(0, command),
            TaskerArg.int_arg(1, timeout),
            TaskerArg.int_arg(2, 1 if use_root else 0),
            TaskerArg.str_arg(3, output_var),
            TaskerArg.str_arg(4),
            TaskerArg.str_arg(5),
            TaskerArg.int_arg(6, 1),
            TaskerArg.int_arg(7, 0),
            TaskerArg.int_arg(8, 0),
        ],
    )


def goto_action(action_number: int) -> TaskerAction:
    """Build a Goto (code 135) action to jump to a specific action number.

    Args:
        action_number: The 1-based action number to jump to.
    """
    return TaskerAction(
        code=CODE_GOTO,
        args=[
            TaskerArg.int_arg(0, 0),  # 0 = Action Number, 1 = Action Label
            TaskerArg.int_arg(1, action_number),
            TaskerArg.str_arg(2, ""),  # label (empty in number mode)
        ],
    )


def termux_self_heal() -> TaskerAction:
    """Build a root Run Shell action that re-applies Termux protections.

    Re-adds Termux to the battery whitelist, disables the phantom
    process killer, grants WRITE_SECURE_SETTINGS (which lets the
    mvwifi_portal wrapper self-heal that setting on every run), and
    resurrects Termux when its process is dead — a dead Termux can
    never run a wrapper to self-heal, so this root shell (which needs
    no Termux) is the only path back.  Idempotent and safe to run on
    every trigger.  continue_on_error keeps the task working on
    unrooted phones — the protections simply can't be re-applied
    without root.
    """
    return run_shell(
        "dumpsys deviceidle whitelist +com.termux; "
        "settings put global settings_enable_monitor_phantom_procs false; "
        "pm grant com.termux android.permission.WRITE_SECURE_SETTINGS; "
        "pidof com.termux >/dev/null 2>&1 || "
        "{ am start -n com.termux/.app.TermuxActivity >/dev/null 2>&1; sleep 3; }",
        timeout=15,
        use_root=True,
        continue_on_error=True,
    )


def termux_task(
    executable: str,
    arguments: str = "",
    background: bool = True,
    timeout: int = 60,
) -> TaskerAction:
    """Build a Termux:Tasker plugin action.

    This uses the Termux:Tasker plugin to execute a script in Termux's
    full environment.  The plugin action format is based on the official
    Termux:Tasker template export.

    Args:
        executable: Script name in ~/.termux/tasker/ (no path needed).
        timeout: Seconds Tasker waits for the plugin to respond. If the
            command takes longer, Tasker reports "plugin did not respond"
            (error code 2). The script keeps running regardless.
        arguments: Arguments to pass to the script (space-separated).
        background: Run in background (no terminal window).
    """
    # Build the Bundle XML matching the official Termux:Tasker template.
    # The Bundle contains plugin-specific extras that Tasker passes to
    # Termux:Tasker's FireReceiver.
    blurb = f"{executable} {arguments}".strip()
    bundle_xml = (
        '<Vals sr="val">'
        f"<com.termux.execute.arguments>{arguments}</com.termux.execute.arguments>"
        "<com.termux.execute.arguments-type>java.lang.String"
        "</com.termux.execute.arguments-type>"
        f"<com.termux.tasker.extra.EXECUTABLE>{executable}"
        "</com.termux.tasker.extra.EXECUTABLE>"
        "<com.termux.tasker.extra.EXECUTABLE-type>java.lang.String"
        "</com.termux.tasker.extra.EXECUTABLE-type>"
        f"<com.termux.tasker.extra.TERMINAL>"
        f"{'false' if background else 'true'}"
        "</com.termux.tasker.extra.TERMINAL>"
        "<com.termux.tasker.extra.TERMINAL-type>java.lang.Boolean"
        "</com.termux.tasker.extra.TERMINAL-type>"
        "<com.termux.tasker.extra.VERSION_CODE>4"
        "</com.termux.tasker.extra.VERSION_CODE>"
        "<com.termux.tasker.extra.VERSION_CODE-type>java.lang.Integer"
        "</com.termux.tasker.extra.VERSION_CODE-type>"
        "<com.termux.tasker.extra.WORKDIR></com.termux.tasker.extra.WORKDIR>"
        "<com.termux.tasker.extra.WORKDIR-type>java.lang.String"
        "</com.termux.tasker.extra.WORKDIR-type>"
        f"<com.twofortyfouram.locale.intent.extra.BLURB>{blurb}"
        "</com.twofortyfouram.locale.intent.extra.BLURB>"
        "<com.twofortyfouram.locale.intent.extra.BLURB-type>java.lang.String"
        "</com.twofortyfouram.locale.intent.extra.BLURB-type>"
        "<net.dinglisch.android.tasker.subbundled>true"
        "</net.dinglisch.android.tasker.subbundled>"
        "<net.dinglisch.android.tasker.subbundled-type>java.lang.Boolean"
        "</net.dinglisch.android.tasker.subbundled-type>"
        "</Vals>"
    )
    return TaskerAction(
        code=CODE_TERMUX_TASK,
        args=[
            TaskerArg.bundle_arg(0, bundle_xml),
            # arg1: plugin package name
            TaskerArg.str_arg(1, "com.termux.tasker"),
            # arg2: plugin config activity
            TaskerArg.str_arg(2, "com.termux.tasker.EditConfigurationActivity"),
            # arg3: action timeout in seconds (verified on-device: the
            # Tasker UI "Timeout" field writes directly to this arg)
            TaskerArg.int_arg(3, timeout),
            # arg4: emitted by Tasker when editing the action
            TaskerArg.int_arg(4, 0),
        ],
    )


# ---------------------------------------------------------------------------
# MVwifiAuto project builder
# ---------------------------------------------------------------------------
def _build_handle_portal_task() -> TaskerTask:
    """Build the HandlePortal task (id=40).

    Uses dynamic portal host detection via redirect URL, matching the
    approach validated in Android devlog Session 17.
    """
    return TaskerTask(
        id=40,
        name="HandlePortal",
        actions=[
            # A1: Wait 3s for WiFi to settle
            wait(seconds=3),
            # A4: HTTP GET 1.1.1.1 — follow redirect to get portal URL
            TaskerAction(
                code=CODE_HTTP_REQUEST,
                continue_on_error=True,
                args=[
                    TaskerArg.bundle_arg(0, _HTTP_BUNDLE_XML),
                    TaskerArg.int_arg(1, 0),
                    TaskerArg.int_arg(10, 0),
                    TaskerArg.int_arg(11, 0),
                    TaskerArg.int_arg(12, 1),
                    TaskerArg.str_arg(
                        2,
                        "http://1.1.1.1/",
                    ),
                    TaskerArg.str_arg(
                        3,
                        "User-Agent:Mozilla/5.0 (Linux; Android 10) "
                        "AppleWebKit/537.36 Chrome/120 Safari/537.36",
                    ),
                    TaskerArg.str_arg(4),
                    TaskerArg.str_arg(5),
                    TaskerArg.str_arg(6),
                    TaskerArg.str_arg(7),
                    TaskerArg.int_arg(8, 10),
                    TaskerArg.int_arg(9, 0),
                ],
            ),
            # A5: Extract portal host:port from redirect URL
            variable_search_replace(
                "%http_response_url",
                "http://([^/]+)/.*",
                "%PortalHost",
            ),
            # A6: HTTP POST to portal acceptance endpoint
            TaskerAction(
                code=CODE_HTTP_REQUEST,
                continue_on_error=True,
                args=[
                    TaskerArg.bundle_arg(0, _HTTP_BUNDLE_XML),
                    TaskerArg.int_arg(1, 1),
                    TaskerArg.int_arg(10, 0),
                    TaskerArg.int_arg(11, 0),
                    TaskerArg.int_arg(12, 1),
                    TaskerArg.str_arg(2, "http://%PortalHost/forms/guest_toued"),
                    TaskerArg.str_arg(
                        3,
                        "Content-Type:application/x-www-form-urlencoded\n"
                        "User-Agent:Mozilla/5.0 (Linux; Android 10) "
                        "AppleWebKit/537.36 Chrome/120 Safari/537.36\n"
                        "Referer:http://%PortalHost/",
                    ),
                    TaskerArg.str_arg(
                        4,
                        "origurl=http%3a%2f%2f1%2e1%2e1%2e1%2f&ok=Accept+and+Continue",
                    ),
                    TaskerArg.str_arg(5),
                    TaskerArg.str_arg(6),
                    TaskerArg.str_arg(7),
                    TaskerArg.int_arg(8, 10),
                    TaskerArg.int_arg(9, 0),
                ],
            ),
            # A8: Verify internet via WiFi
            TaskerAction(
                code=CODE_HTTP_REQUEST,
                args=[
                    TaskerArg.bundle_arg(0, _HTTP_BUNDLE_XML),
                    TaskerArg.int_arg(1, 0),
                    TaskerArg.int_arg(10, 0),
                    TaskerArg.int_arg(11, 0),
                    TaskerArg.int_arg(12, 1),
                    TaskerArg.str_arg(2, "http://detectportal.firefox.com/success.txt"),
                    TaskerArg.str_arg(3),
                    TaskerArg.str_arg(4),
                    TaskerArg.str_arg(5),
                    TaskerArg.str_arg(6),
                    TaskerArg.str_arg(7),
                    TaskerArg.int_arg(8, 10),
                    TaskerArg.int_arg(9, 0),
                ],
            ),
            # A9: Store verify result
            variable_set("%VerifyResult", "%http_response_code"),
            # A10: If 200 — success
            if_condition("%VerifyResult", "200"),
            flash("Connected with internet!"),
            else_action(),
            flash("Portal acceptance failed"),
            end_if(),
        ],
    )


def _build_connect_to_cmvwifi_task() -> TaskerTask:
    """Build the ConnectToCmvwifi task (id=50)."""
    return TaskerTask(
        id=50,
        name="ConnectToCmvwifi",
        actions=[
            # A1: If already connected to cmvwifi
            if_condition("%WIFII", "cmvwifi"),
            # A2: Already connected, go straight to portal
            perform_task("HandlePortal", wait_for_finish=True),
            # A3: Else - not connected
            else_action(),
            # A4: Connect to cmvwifi
            connect_wifi("cmvwifi"),
            # A5: Wait 5 seconds
            wait(seconds=5),
            # A6: Call HandlePortal
            perform_task("HandlePortal", wait_for_finish=True),
            # A7: End If
            end_if(),
        ],
    )


def _build_cmvwifi_profile() -> TaskerProfile:
    """Build the cmvwifi Auto Connect profile (id=1)."""
    return TaskerProfile(
        id=1,
        name="cmvwifi Auto Connect",
        state=TaskerState(
            code=CODE_WIFI_NEAR_STATE,
            args=[
                TaskerArg.str_arg(0, "cmvwifi"),   # SSID
                TaskerArg.str_arg(1),              # MAC (empty = any)
                TaskerArg.str_arg(2),              # Capabilities (empty = any)
                TaskerArg.int_arg(3, 0),           # Min Activate Signal Level
                TaskerArg.int_arg(4, 0),           # Channel (0 = any)
                TaskerArg.int_arg(5, 0),           # Toggle WiFi (0 = off)
            ],
        ),
        task_id=50,
    )


def build_mvwifi_project() -> TaskerProject:
    """Build the complete MVwifiAuto Tasker project (pure-Tasker approach).

    This is the original pure-Tasker approach using HTTP Request actions
    for portal handling.  It does NOT work on Android 16 due to policy
    routing (see android-devlog.md Sessions 14-17).  Kept for reference
    and for devices where the policy routing issue doesn't apply.

    Returns:
        A :class:`TaskerProject` containing all tasks and the WiFi Near
        profile, ready for XML generation.
    """
    tasks = [
        _build_handle_portal_task(),
        _build_connect_to_cmvwifi_task(),
    ]
    profiles = [_build_cmvwifi_profile()]
    return TaskerProject(
        name="MVwifiAuto",
        profiles=profiles,
        tasks=tasks,
    )


# ---------------------------------------------------------------------------
# Termux project builder (recommended approach)
# ---------------------------------------------------------------------------
def _build_run_portal_script_task_termux() -> TaskerTask:
    """Build the MVW_RunPortalScript task (id=70) — Termux:Tasker plugin.

    This task runs the Python portal handler via the Termux:Tasker plugin.
    The plugin executes the wrapper script at ~/.termux/tasker/mvwifi_portal
    in Termux's full environment.
    """
    return TaskerTask(
        id=70,
        name="MVW_RunPortalScript",
        actions=[
            # A1: Run the portal handler via Termux:Tasker plugin
            _lbl(
                termux_task("mvwifi_portal", background=True),
                "cmvwifi portal handler — runs ~/.termux/tasker/mvwifi_portal",
            ),
        ],
    )


def _build_connect_and_run_task_termux() -> TaskerTask:
    """Build the MVW_ConnectAndRun task (id=80) — Termux approach.

    Connects to cmvwifi (if not already connected), waits for DHCP,
    then calls MVW_RunPortalScript to handle the captive portal.
    """
    return TaskerTask(
        id=80,
        name="MVW_ConnectAndRun",
        actions=[
            # A1: History marker — records that Tasker fired, even if
            # the plugin call later fails or Termux never runs.
            # Lands at /sdcard/Tasker/mvwifi_history.log.
            _lbl(
                write_file(
                    "Tasker/mvwifi_history.log",
                    "%TIMES | tasker | MVW_ConnectAndRun fired [gen @MWGEN@]",
                ),
                "History marker — proves Tasker fired even if Termux fails",
            ),
            # A2: Store current SSID
            variable_set("%CurrentSSID", "%WIFII"),
            # A3: If already on cmvwifi, skip connection
            if_condition("%CurrentSSID", "cmvwifi"),
            # A4: Goto A11 (self-heal) — skip connect and wait
            goto_action(11),
            # A5: End If
            end_if(),
            # A6: If already on MVwifi — same municipal network under
            # its alternate SSID — skip connection too
            _lbl(
                if_condition("%CurrentSSID", "MVwifi"),
                "MVwifi = same municipal network, alternate SSID",
            ),
            # A7: Goto A11 (self-heal)
            goto_action(11),
            # A8: End If
            end_if(),
            # A9: Connect to cmvwifi.  continue_on_error is REQUIRED:
            # the nudge can associate before this delayed event task
            # runs, and %WIFII can be ">>> SCAN <<<" (failing the A3/A6
            # skip guards) — a redundant connect then errors and would
            # otherwise abort the task before the portal handler.
            connect_wifi("cmvwifi", continue_on_error=True),
            # A10: Wait 5 seconds for DHCP
            _lbl(wait(seconds=5), "Let DHCP settle"),
            # A11: Re-apply Termux protections (root; skipped on
            # unrooted devices via continue_on_error)
            _lbl(
                termux_self_heal(),
                "Re-apply Termux protections (root; no-op unrooted)",
            ),
            # A12: Run the portal script
            _lbl(
                perform_task("MVW_RunPortalScript", wait_for_finish=True),
                "Portal handler via Termux:Tasker",
            ),
            # A13: Flash completion
            flash("Portal handling complete"),
        ],
    )


def _build_cmvwifi_profile_termux() -> TaskerProfile:
    """Build the cmvwifi Auto Connect profile (id=2) — Termux approach.

    Uses WiFi Connected rather than WiFi Near: Android auto-joins
    cmvwifi itself, and WiFi Connected fires on the association event
    immediately. WiFi Near depends on WiFi scans, which Android
    throttles to ~1 per 30 min for background apps — observed as a
    ~29-minute delay between association and portal handling while
    the phone was idle.
    """
    return TaskerProfile(
        id=2,
        name="MVW cmvwifi Auto Connect",
        state=TaskerState(
            code=CODE_WIFI_CONNECTED_STATE,
            args=[
                TaskerArg.str_arg(0, "cmvwifi"),   # SSID
                TaskerArg.str_arg(1),              # MAC (empty = any)
                TaskerArg.str_arg(2),              # IP (empty = any)
                TaskerArg.int_arg(3, 2),           # Active (2 = connected)
            ],
        ),
        task_id=80,  # MVW_ConnectAndRun
    )


def _build_mvwifi_profile_termux() -> TaskerProfile:
    """Build the MVwifi Auto Connect profile (id=6) — Termux approach.

    `MVwifi` is the same municipal network broadcasting under an
    alternate SSID — observed on-device (09:30 association during a
    field session).  Fires MVW_ConnectAndRun exactly like the cmvwifi
    profile; the task skips re-connecting when %WIFII already names
    either SSID, so the portal handler just runs.
    """
    return TaskerProfile(
        id=6,
        name="MVW MVwifi Auto Connect",
        state=TaskerState(
            code=CODE_WIFI_CONNECTED_STATE,
            args=[
                TaskerArg.str_arg(0, "MVwifi"),   # SSID
                TaskerArg.str_arg(1),              # MAC (empty = any)
                TaskerArg.str_arg(2),              # IP (empty = any)
                TaskerArg.int_arg(3, 2),           # Active (2 = connected)
            ],
        ),
        task_id=80,  # MVW_ConnectAndRun
    )


def _build_costco_connect_task() -> TaskerTask:
    """Build the MVW_CostcoConnect task (id=90) — Termux portal handler.

    Runs the Costco captive-portal handler via the Termux:Tasker
    plugin.  The Costco portal is a Juniper Mist TOS-accept page —
    POST-replayable, no app/login needed (captured 2026-10-01).
    """
    return TaskerTask(
        id=90,
        name="MVW_CostcoConnect",
        actions=[
            # A1: History marker (same shared log as MVW_ConnectAndRun).
            _lbl(
                write_file(
                    "Tasker/mvwifi_history.log",
                    "%TIMES | tasker | MVW_CostcoConnect fired [gen @MWGEN@]",
                ),
                "History marker — proves Tasker fired even if Termux fails",
            ),
            # A2: Re-apply Termux protections before the plugin call.
            _lbl(
                termux_self_heal(),
                "Re-apply Termux protections (root; no-op unrooted)",
            ),
            # A3: Run the Costco portal handler.
            _lbl(
                termux_task("costco_portal", background=True),
                "Mist TOS-accept handler — runs ~/.termux/tasker/costco_portal",
            ),
            # A4: Flash completion.
            flash("Costco portal handled"),
        ],
    )


def _build_costco_profile_termux() -> TaskerProfile:
    """Build the Costco Member Wifi profile (id=3) — Termux approach.

    Fires on association with the Costco SSID and runs MVW_CostcoConnect,
    which POSTs the Mist TOS-accept form and verifies connectivity.
    """
    return TaskerProfile(
        id=3,
        name="MVW Costco WiFi Connected",
        state=TaskerState(
            code=CODE_WIFI_CONNECTED_STATE,
            args=[
                TaskerArg.str_arg(0, "Costco Member Wifi"),  # SSID
                TaskerArg.str_arg(1),                        # MAC (any)
                TaskerArg.str_arg(2),                        # IP (any)
                TaskerArg.int_arg(3, 2),                     # Active
            ],
        ),
        task_id=90,  # MVW_CostcoConnect
    )


def _build_cmvwifi_nudge_task() -> TaskerTask:
    """Build the MVW_NudgeWifi task (id=100) — periodic auto-join nudge.

    Runs the wifi_nudge wrapper via the Termux:Tasker plugin with
    ``--preferred --fallback xfinitywifi``: targets the open preferred
    tier (cmvwifi, MVwifi, Costco Member Wifi — the module's
    PREFERRED_OPEN_SSIDS; ``--preferred`` exists because the Costco
    SSID's spaces can't survive Termux's space-separated Arguments
    field).  Joins a visible-but-unassociated preferred network,
    bypassing the selector's auto-join backoff, and promotes the
    device off xfinitywifi onto whichever preferred SSID is in range.
    """
    return TaskerTask(
        id=100,
        name="MVW_NudgeWifi",
        actions=[
            # A1: History marker (same shared log as MVW_ConnectAndRun).
            _lbl(
                write_file(
                    "Tasker/mvwifi_history.log",
                    "%TIMES | tasker | MVW_NudgeWifi fired [gen @MWGEN@]",
                ),
                "History marker — proves Tasker fired even if Termux fails",
            ),
            # A2: Re-apply Termux protections + resurrect a dead
            # Termux before the plugin call — plugin actions report
            # OK even when Termux never executes the wrapper.
            _lbl(
                termux_self_heal(),
                "Re-apply Termux protections + revive Termux (root; "
                "no-op unrooted)",
            ),
            # A3: Run the nudge script.  A short timeout is fine —
            # background mode keeps the script running regardless.
            _lbl(
                termux_task(
                    "wifi_nudge",
                    arguments="--preferred --fallback xfinitywifi",
                    background=True,
                    timeout=45,
                ),
                "Open-preferred nudge (cmvwifi/MVwifi/Costco) + xfinity "
                "promotion (root) — runs ~/.termux/tasker/wifi_nudge",
            ),
        ],
    )


def _build_cmvwifi_nudge_profile() -> TaskerProfile:
    """Build the cmvwifi Periodic Nudge profile (id=4).

    A Time context repeating every 15 minutes, all day.  Android's
    screen-off PNO scans are throttled and the network selector backs
    off cmvwifi after portal "no internet" verdicts, so periodic
    nudging covers the gap that WiFi Near (same throttle) and
    Display On (only helps when the screen is touched) leave.
    Battery cost is one scan and an occasional connect request.
    """
    return TaskerProfile(
        id=4,
        name="MVW cmvwifi Periodic Nudge",
        state=TaskerTime(repeat_unit=2, repeat_value=15),
        task_id=100,  # MVW_NudgeWifi
    )


def _build_xfinity_nudge_task() -> TaskerTask:
    """Build the MVW_NudgeXfinity task (id=110) — fallback-only join.

    Runs wifi_nudge with ``--ssid xfinitywifi --autojoin-disabled
    --defer-to-preferred``: joins open xfinitywifi only when fully
    disconnected *and* no preferred network is visible (the defer
    check prevents the nudge tasks racing connect requests in the
    same tick — observed at Costco when xfinity was requested while
    Costco Member Wifi was in range).  ``--defer-to-preferred``
    expands the module's PREFERRED_SSIDS constant — needed because
    Termux's Arguments field can't carry the multi-word "Costco
    Member Wifi" SSID safely.  Marks the saved config
    autojoin-disabled (-d) so Android never hops onto it while a
    better network is around.  Disabling the profile in Tasker
    excludes xfinitywifi entirely — preferred-network handling (and
    promotion away from xfinitywifi) is unaffected.
    """
    return TaskerTask(
        id=110,
        name="MVW_NudgeXfinity",
        actions=[
            _lbl(
                write_file(
                    "Tasker/mvwifi_history.log",
                    "%TIMES | tasker | MVW_NudgeXfinity fired [gen @MWGEN@]",
                ),
                "History marker — proves Tasker fired even if Termux fails",
            ),
            # Re-apply Termux protections + resurrect a dead Termux
            # before the plugin call.
            _lbl(
                termux_self_heal(),
                "Re-apply Termux protections + revive Termux (root; "
                "no-op unrooted)",
            ),
            _lbl(
                termux_task(
                    "wifi_nudge",
                    arguments=(
                        "--ssid xfinitywifi --autojoin-disabled "
                        "--defer-to-preferred"
                    ),
                    background=True,
                    timeout=45,
                ),
                "xfinitywifi fallback join, autojoin off, defers to "
                "preferred SSIDs (root) — runs ~/.termux/tasker/wifi_nudge",
            ),
        ],
    )


def _build_show_version_task() -> TaskerTask:
    """Build the MVW_ShowVersion task (id=120) — manual gen-id check.

    Flashes the generation stamp of the imported project.  Run it by
    hand in Tasker (▶) to answer "which generation is actually
    imported?" — compare against the ``gen`` value in the marker
    lines of a freshly generated XML.  Detects stale/merged imports
    in one tap, no adb needed.
    """
    return TaskerTask(
        id=120,
        name="MVW_ShowVersion",
        actions=[
            _lbl(
                flash(f"MVwifiAuto-Termux gen {GEN_PLACEHOLDER}"),
                "Shows the imported project's generation stamp",
            ),
        ],
    )


def _build_shguest_capture_task() -> TaskerTask:
    """Build the MVW_SHGuestCapture task (id=130) — portal recon, not login.

    Runs the shguest_probe wrapper via the Termux:Tasker plugin when
    the phone associates to SHGuestNet (healthcare guest
    WiFi).  The probe captures the portal's redirect chain, HTML,
    form fields (incl. checkboxes), JS assets and any deep-link
    intents to ~/storage/shared/shguestnet_capture/ — everything
    needed to write an auto-accept handler later.  It does NOT accept
    the portal; the user still taps through it this visit.
    """
    return TaskerTask(
        id=130,
        name="MVW_SHGuestCapture",
        actions=[
            # A1: History marker.
            _lbl(
                write_file(
                    "Tasker/mvwifi_history.log",
                    "%TIMES | tasker | MVW_SHGuestCapture fired [gen @MWGEN@]",
                ),
                "History marker — proves Tasker fired even if Termux fails",
            ),
            # A2: Re-apply Termux protections before the plugin call.
            _lbl(
                termux_self_heal(),
                "Re-apply Termux protections (root; no-op unrooted)",
            ),
            # A3: Run the capture probe. 90s covers the 25s post-launch
            # wait plus asset fetches; background mode keeps running
            # regardless.
            _lbl(
                termux_task("shguest_probe", background=True, timeout=90),
                "Portal capture probe — runs ~/.termux/tasker/shguest_probe",
            ),
            # A4: Flash so the user knows a capture just happened.
            flash("SHGuestNet portal captured"),
        ],
    )


def _build_shguest_profile() -> TaskerProfile:
    """Build the SHGuestNet WiFi Connected profile (id=7).

    Fires on association with the clinic guest SSID and runs
    MVW_SHGuestCapture — the automated portal recon for a future
    auto-accept handler.
    """
    return TaskerProfile(
        id=7,
        name="MVW SHGuestNet WiFi Connected",
        state=TaskerState(
            code=CODE_WIFI_CONNECTED_STATE,
            args=[
                TaskerArg.str_arg(0, "SHGuestNet"),  # SSID
                TaskerArg.str_arg(1),                 # MAC (any)
                TaskerArg.str_arg(2),                 # IP (any)
                TaskerArg.int_arg(3, 2),              # Active
            ],
        ),
        task_id=130,  # MVW_SHGuestCapture
    )


def _build_xfinity_nudge_profile() -> TaskerProfile:
    """Build the xfinitywifi Periodic Nudge profile (id=5).

    Same 15-minute Time context as the cmvwifi nudge.  xfinitywifi
    is a fallback network: the nudge only joins it when nothing else
    is connected, and its saved config is autojoin-disabled so
    Android itself never picks it.  Toggle this profile off in
    Tasker to stop all xfinitywifi use.
    """
    return TaskerProfile(
        id=5,
        name="MVW xfinitywifi Periodic Nudge",
        state=TaskerTime(repeat_unit=2, repeat_value=15),
        task_id=110,  # MVW_NudgeXfinity
    )


def _build_unlock_nudge_task() -> TaskerTask:
    """Build the MVW_UnlockNudge task (id=140) — intent-driven nudge.

    Fires the same wifi_nudge wrapper as MVW_NudgeWifi but with
    ``--min-interval 300``: at unlock the status check runs (cheap),
    and a real scan+connect happens at most once per five minutes
    across *all* callers, so a burst of unlocks can't thrash the
    radio.  Covers the gap the 15-minute poll leaves between arrival
    and the next tick — the moment the user actually needs
    connectivity is when they unlock the phone.
    """
    return TaskerTask(
        id=140,
        name="MVW_UnlockNudge",
        actions=[
            _lbl(
                write_file(
                    "Tasker/mvwifi_history.log",
                    "%TIMES | tasker | MVW_UnlockNudge fired [gen @MWGEN@]",
                ),
                "History marker — proves Tasker fired even if Termux fails",
            ),
            # Re-apply Termux protections + resurrect a dead Termux
            # before the plugin call.
            _lbl(
                termux_self_heal(),
                "Re-apply Termux protections + revive Termux (root; "
                "no-op unrooted)",
            ),
            _lbl(
                termux_task(
                    "wifi_nudge",
                    arguments=(
                        "--preferred --fallback xfinitywifi "
                        "--min-interval 300"
                    ),
                    background=True,
                    timeout=45,
                ),
                "Throttled open-preferred nudge on unlock (root) — runs "
                "~/.termux/tasker/wifi_nudge",
            ),
        ],
    )


def _build_unlock_profile() -> TaskerProfile:
    """Build the Display Unlocked Nudge profile (id=8).

    Event → Display Unlocked (code 1000, user_present) fires once per
    unlock — the cheapest possible intent signal.  The task's own
    --min-interval throttle bounds scan frequency, and the nudge
    no-ops whenever already connected.
    """
    return TaskerProfile(
        id=8,
        name="MVW Display Unlocked Nudge",
        state=TaskerEvent(code=CODE_DISPLAY_UNLOCKED_EVENT),
        task_id=140,  # MVW_UnlockNudge
    )


def build_watchdog_project() -> TaskerProject:
    """Build the standalone watchdog project — lives OUTSIDE
    MVwifiAuto-Termux on purpose.

    The worst failure mode observed on-device (Oct 13) is the whole
    MVwifiAuto-Termux project being deleted from Tasker during
    unrelated project work — after which *nothing* writes history
    markers, so the breakage is invisible. This project exists
    separately so it survives that deletion: every 6h it stats the
    history log's mtime and posts a Notify if nothing has written
    for >24h (long enough to tolerate the phone being off
    overnight).  It also stats the termux-side nudge log — Tasker
    markers keep the history fresh even when a dead Termux runs
    nothing, so both ages are checked; which side went silent is
    recorded as a ``watchdog | stale`` line in the history log.

    Task: ``MVW_HistoryWatchdog`` (id=200)
    Profile: ``MVW History Watchdog`` (id=20) — Time every 6h
    """
    task = TaskerTask(
        id=200,
        name="MVW_HistoryWatchdog",
        actions=[
            _lbl(
                run_shell(
                    "H=/sdcard/Tasker/mvwifi_history.log; "
                    "N=/sdcard/mvwifi_nudge.log; "
                    "HAGE=$(( $(date +%s) - $(stat -c %Y $H "
                    "2>/dev/null || echo 0) )); "
                    "NAGE=$(( $(date +%s) - $(stat -c %Y $N "
                    "2>/dev/null || echo 0) )); "
                    "DETAIL=; "
                    "if [ $HAGE -gt 86400 ]; then DETAIL=tasker; fi; "
                    "if [ $NAGE -gt 86400 ]; then "
                    "DETAIL=\"$DETAIL termux\"; fi; "
                    "if [ -n \"$DETAIL\" ]; then "
                    "echo \"$(date +%s) | watchdog | stale:$DETAIL\" >> $H; "
                    "echo STALE; fi",
                    output_var="%output",
                ),
                "Log age check — echoes STALE if Tasker markers or "
                "Termux nudges go quiet for >24h",
            ),
            _lbl(
                if_condition("%output", "STALE"),
                "Alert only when the log has gone quiet",
            ),
            notify(
                "MVwifiAuto silent",
                "No Tasker/Termux activity in >24h — check "
                "MVwifiAuto-Termux is still imported; if only Termux "
                "markers are stale, Termux itself may be dead (see "
                "'watchdog | stale' lines in mvwifi_history.log)",
            ),
            flash("MVwifiAuto watchdog: history log stale >24h"),
            end_if(),
        ],
    )
    profile = TaskerProfile(
        id=20,
        name="MVW History Watchdog",
        state=TaskerTime(repeat_unit=1, repeat_value=6),
        task_id=200,
    )
    return TaskerProject(
        name="MVwifiWatchdog",
        profiles=[profile],
        tasks=[task],
    )


def build_termux_project() -> TaskerProject:
    """Build the MVwifiAuto Tasker project for the Termux approach.

    This is the recommended approach.  Tasker handles WiFi detection
    and connection; the Python script (via Termux:Tasker plugin) handles
    the captive portal with SO_BINDTODEVICE interface binding.

    Tasks:
        - MVW_RunPortalScript: runs wpa-android via Termux:Tasker plugin
        - MVW_ConnectAndRun: connects to cmvwifi (or skips when already on
          cmvwifi/MVwifi), waits for DHCP, calls MVW_RunPortalScript
        - MVW_CostcoConnect: runs the Costco Mist portal handler
        - MVW_NudgeWifi: periodic auto-join nudge for cmvwifi (+ promotes
          off xfinitywifi when cmvwifi is visible)
        - MVW_NudgeXfinity: periodic fallback nudge for xfinitywifi,
          joined with autojoin disabled (-d)
        - MVW_ShowVersion: flashes the generation stamp — run manually to
          verify which project generation is actually imported
        - MVW_SHGuestCapture: portal capture probe for SHGuestNet (healthcare guest WiFi) — recon only, does not accept the portal
        - MVW_UnlockNudge: same nudge as MVW_NudgeWifi plus
          --min-interval 300 — fired on Display Unlocked

    Profiles:
        - MVW cmvwifi Auto Connect: WiFi Connected → MVW_ConnectAndRun
        - MVW MVwifi Auto Connect: WiFi Connected (MVwifi — same
          municipal network, alternate SSID) → MVW_ConnectAndRun
        - MVW Costco WiFi Connected: WiFi Connected (Costco Member
          Wifi) → MVW_CostcoConnect
        - MVW cmvwifi Periodic Nudge: Time (every 15 min) →
          MVW_NudgeWifi
        - MVW xfinitywifi Periodic Nudge: Time (every 15 min) →
          MVW_NudgeXfinity
        - MVW SHGuestNet WiFi Connected: WiFi Connected (SHGuestNet) →
          MVW_SHGuestCapture
        - MVW Display Unlocked Nudge: Display Unlocked event →
          MVW_UnlockNudge (throttled to one scan per 5 min)

    Returns:
        A :class:`TaskerProject` ready for XML generation.
    """
    tasks = [
        _build_run_portal_script_task_termux(),
        _build_connect_and_run_task_termux(),
        _build_costco_connect_task(),
        _build_cmvwifi_nudge_task(),
        _build_xfinity_nudge_task(),
        _build_show_version_task(),
        _build_shguest_capture_task(),
        _build_unlock_nudge_task(),
    ]
    profiles = [
        _build_cmvwifi_profile_termux(),
        _build_mvwifi_profile_termux(),
        _build_costco_profile_termux(),
        _build_cmvwifi_nudge_profile(),
        _build_xfinity_nudge_profile(),
        _build_shguest_profile(),
        _build_unlock_profile(),
    ]
    return TaskerProject(
        name="MVwifiAuto-Termux",
        profiles=profiles,
        tasks=tasks,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """CLI entry point for generating the Tasker XML.

    Args:
        argv: Command-line arguments (default: ``sys.argv[1:]``).

    Returns:
        0 on success.
    """
    parser = argparse.ArgumentParser(
        description="Generate the MVwifiAuto Tasker .prj.xml file",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="android/MVwifiAuto.prj.xml",
        help="Output file path (default: android/MVwifiAuto.prj.xml)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print XML to stdout instead of writing a file",
    )
    parser.add_argument(
        "--termux",
        action="store_true",
        help="Generate the Termux approach project (recommended) instead of "
        "the pure-Tasker approach",
    )
    parser.add_argument(
        "--watchdog",
        action="store_true",
        help="Generate the standalone MVwifiWatchdog project (silence "
        "detector — survives deletion of the main project)",
    )

    args = parser.parse_args(argv)

    if args.watchdog:
        project = build_watchdog_project()
        if args.output == "android/MVwifiAuto.prj.xml":
            args.output = "android/MVwifiWatchdog.prj.xml"
    elif args.termux:
        project = build_termux_project()
        if args.output == "android/MVwifiAuto.prj.xml":
            args.output = "android/MVwifiAuto-Termux.prj.xml"
    else:
        project = build_mvwifi_project()

    xml_text = generate_project_xml(project)

    if args.stdout:
        print(xml_text)
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(xml_text)
        print(f"Written {len(xml_text)} bytes to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
