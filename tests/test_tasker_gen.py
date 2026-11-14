"""Tests for the Tasker XML generator."""

from xml.dom.minidom import parseString
from xml.etree.ElementTree import ParseError, fromstring, tostring

import pytest

from wifi_portal_automation.tasker_gen import (
    CODE_CONNECT_WIFI,
    CODE_ELSE,
    CODE_END_IF,
    CODE_FLASH,
    CODE_GOTO,
    CODE_HTTP_REQUEST,
    CODE_IF,
    CODE_PERFORM_TASK,
    CODE_RUN_SHELL,
    CODE_STOP,
    CODE_TERMUX_TASK,
    CODE_VARIABLE_SEARCH_REPLACE,
    CODE_VARIABLE_SET,
    CODE_VARIABLE_SPLIT,
    CODE_WAIT,
    CODE_WIFI_CONNECTED_STATE,
    CODE_WIFI_NEAR_STATE,
    CODE_WRITE_FILE,
    COLLISION_ABORT_NEW,
    OP_EQUALS,
    TaskerAction,
    TaskerArg,
    TaskerProject,
    TaskerTask,
    build_mvwifi_project,
    build_termux_project,
    build_watchdog_project,
    connect_wifi,
    else_action,
    end_if,
    flash,
    generate_project_xml,
    goto_action,
    http_request,
    if_condition,
    perform_task,
    run_shell,
    stop,
    termux_task,
    variable_search_replace,
    variable_set,
    wait,
    write_file,
)


class TestActionCodes:
    """Pin every action/state code to its literal Tasker value.

    Asserting `action.code == CODE_X` is circular — it reads the
    constant being tested, so a wrong constant still passes (this
    is how CODE_GOTO = 731/Take Call survived undetected). The
    literals below are the ground truth from Tasker's action code
    table and verified real exports:
    https://github.com/Taskomater/Tasker-XML-Info/blob/master/Tasker_XML_Codes.md
    """

    @pytest.mark.parametrize(
        "constant,expected",
        [
            (CODE_IF, 37),
            (CODE_END_IF, 38),
            (CODE_ELSE, 43),
            (CODE_WAIT, 30),
            (CODE_STOP, 137),
            (CODE_PERFORM_TASK, 130),
            (CODE_RUN_SHELL, 123),
            (CODE_VARIABLE_SET, 547),
            (CODE_FLASH, 548),
            (CODE_VARIABLE_SPLIT, 590),
            (CODE_VARIABLE_SEARCH_REPLACE, 598),
            (CODE_HTTP_REQUEST, 339),
            (CODE_CONNECT_WIFI, 398),
            (CODE_WIFI_NEAR_STATE, 170),
            (CODE_WIFI_CONNECTED_STATE, 160),
            (CODE_GOTO, 135),
            (CODE_WRITE_FILE, 410),
            (CODE_TERMUX_TASK, 1256900802),
        ],
    )
    def test_code_matches_tasker_table(self, constant, expected):
        assert constant == expected


class TestArgBuilders:
    """Test argument builder helpers."""

    def test_str_arg(self):
        arg = TaskerArg.str_arg(0, "hello")
        assert arg.index == 0
        assert arg.kind == "Str"
        assert arg.value == "hello"

    def test_int_arg(self):
        arg = TaskerArg.int_arg(1, 42)
        assert arg.index == 1
        assert arg.kind == "Int"
        assert arg.value == "42"

    def test_bundle_arg(self):
        arg = TaskerArg.bundle_arg(0, "<Vals/>")
        assert arg.index == 0
        assert arg.kind == "Bundle"
        assert arg.value == "<Vals/>"


class TestActionBuilders:
    """Test action builder helpers."""

    def test_perform_task(self):
        action = perform_task("SomeTask", par1="hello")
        assert action.code == CODE_PERFORM_TASK
        assert action.args[0].value == "SomeTask"
        assert action.args[2].value == "hello"
        assert action.args[10].value == "1"  # wait_for_finish

    def test_flash(self):
        action = flash("test message")
        assert action.code == CODE_FLASH
        assert action.args[0].value == "test message"

    def test_variable_set(self):
        action = variable_set("%Foo", "bar")
        assert action.code == CODE_VARIABLE_SET
        assert action.args[0].value == "%Foo"
        assert action.args[1].value == "bar"

    def test_if_condition(self):
        action = if_condition("%WIFII", "cmvwifi")
        assert action.code == CODE_IF
        assert action.conditions is not None
        assert action.conditions[0].lhs == "%WIFII"
        assert action.conditions[0].op == OP_EQUALS
        assert action.conditions[0].rhs == "cmvwifi"

    def test_end_if(self):
        action = end_if()
        assert action.code == CODE_END_IF
        assert action.args == []

    def test_else_action(self):
        action = else_action()
        assert action.code == CODE_ELSE

    def test_wait(self):
        action = wait(seconds=5)
        assert action.code == CODE_WAIT
        assert action.args[1].value == "5"  # seconds

    def test_wait_minutes(self):
        action = wait(minutes=3)
        assert action.code == CODE_WAIT
        assert action.args[2].value == "3"  # minutes

    def test_stop(self):
        action = stop()
        assert action.code == CODE_STOP

    def test_variable_search_replace(self):
        action = variable_search_replace("%URL", "http://([^/]+)/.*", "%Host")
        assert action.code == CODE_VARIABLE_SEARCH_REPLACE
        assert action.args[0].value == "%URL"
        assert action.args[1].value == "http://([^/]+)/.*"
        assert action.args[2].value == "%Host"

    def test_http_request_get(self):
        action = http_request(method="GET", url="http://1.1.1.1/")
        assert action.code == CODE_HTTP_REQUEST
        assert action.args[1].value == "0"  # method=GET
        assert action.args[2].value == "http://1.1.1.1/"

    def test_http_request_post(self):
        action = http_request(
            method="POST",
            url="http://10.64.2.21:9997/forms/guest_toued",
            headers="Content-Type:application/x-www-form-urlencoded",
            body="origurl=test&ok=Accept",
        )
        assert action.code == CODE_HTTP_REQUEST
        assert action.args[1].value == "1"  # method=POST
        assert action.args[3].value == "Content-Type:application/x-www-form-urlencoded"
        assert action.args[4].value == "origurl=test&ok=Accept"

    def test_http_request_no_redirects(self):
        action = http_request(follow_redirects=False)
        assert action.args[9].value == "1"  # redirects off

    def test_connect_wifi(self):
        action = connect_wifi("cmvwifi")
        assert action.code == CODE_CONNECT_WIFI
        assert action.args[0].value == "cmvwifi"

    def test_run_shell(self):
        action = run_shell("echo hello", timeout=5, output_var="%Result")
        assert action.code == CODE_RUN_SHELL
        assert action.args[0].value == "echo hello"
        assert action.args[1].value == "5"  # timeout
        assert action.args[2].value == "0"  # use_root=False
        assert action.args[3].value == "%Result"


class TestXmlGeneration:
    """Test XML generation from data structures."""

    def test_generates_valid_xml(self):
        """Test that the generated XML is well-formed."""
        project = build_mvwifi_project()
        xml_text = generate_project_xml(project)
        # Should parse without error
        root = fromstring(xml_text)
        assert root.tag == "TaskerData"

    def test_has_xml_declaration(self):
        """Test that the XML starts with a declaration."""
        project = build_mvwifi_project()
        xml_text = generate_project_xml(project)
        assert xml_text.startswith('<?xml version="1.0" encoding="UTF-8"?>')

    def test_root_attributes(self):
        """Test that the root element has correct attributes."""
        project = build_mvwifi_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        assert root.get("sr") == ""
        assert root.get("dvi") == "1"
        assert root.get("tv") == "5.15.14"

    def test_project_element(self):
        """Test that the Project element exists with correct name."""
        project = build_mvwifi_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        proj = root.find("Project")
        assert proj is not None
        assert proj.find("name").text == "MVwifiAuto"

    def test_profile_element(self):
        """Test that the WiFi Near profile exists and links to task 50."""
        project = build_mvwifi_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        profile = root.find("Profile")
        assert profile is not None
        assert profile.find("nme").text == "cmvwifi Auto Connect"
        assert profile.find("mid0").text == "50"
        state = profile.find("State")
        assert state is not None
        assert state.find("code").text == str(CODE_WIFI_NEAR_STATE)

    def test_wifi_near_state_args(self):
        """Test WiFi Near state has correct parameter order and types.

        WiFi Near state args must be:
          arg0: SSID (Str), arg1: MAC (Str), arg2: Capabilities (Str),
          arg3: Min Signal (Int), arg4: Channel (Int), arg5: Toggle WiFi (Int)
        """
        project = build_mvwifi_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        state = root.find("Profile").find("State")
        assert state is not None
        args = state.findall("Str") + state.findall("Int")
        # Should have 6 args: 3 Str + 3 Int
        assert len(args) == 6
        # arg0: SSID = "cmvwifi" (Str)
        assert state.find('Str[@sr="arg0"]').text == "cmvwifi"
        # arg1: MAC = "" (Str, empty = any)
        assert state.find('Str[@sr="arg1"]').text in (None, "")
        # arg2: Capabilities = "" (Str, empty = any)
        assert state.find('Str[@sr="arg2"]').text in (None, "")
        # arg3: Min Signal = 0 (Int)
        assert state.find('Int[@sr="arg3"]').get("val") == "0"
        # arg4: Channel = 0 (Int)
        assert state.find('Int[@sr="arg4"]').get("val") == "0"
        # arg5: Toggle WiFi = 0 (Int)
        assert state.find('Int[@sr="arg5"]').get("val") == "0"

    def test_all_tasks_present(self):
        """Test that the production tasks are present in the XML."""
        project = build_mvwifi_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        tasks = root.findall("Task")
        assert len(tasks) == 2
        task_names = {t.find("nme").text for t in tasks}
        assert task_names == {
            "HandlePortal",
            "ConnectToCmvwifi",
        }

    def test_task_ids(self):
        """Test that task IDs match expected values."""
        project = build_mvwifi_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        tasks = root.findall("Task")
        task_ids = {int(t.find("id").text) for t in tasks}
        assert task_ids == {40, 50}

    def test_handle_portal_has_http_requests(self):
        """Test that HandlePortal has HTTP Request actions."""
        project = build_mvwifi_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        tasks = root.findall("Task")
        handle_portal = next(t for t in tasks if t.find("nme").text == "HandlePortal")
        actions = handle_portal.findall("Action")
        http_actions = [a for a in actions if a.find("code").text == str(CODE_HTTP_REQUEST)]
        # Should have at least 3 HTTP requests: GET 1.1.1.1, POST portal, GET verify
        assert len(http_actions) >= 3

    def test_connect_to_cmvwifi_has_if_else(self):
        """Test that ConnectToCmvwifi has If/Else/End If structure."""
        project = build_mvwifi_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        tasks = root.findall("Task")
        connect_task = next(t for t in tasks if t.find("nme").text == "ConnectToCmvwifi")
        actions = connect_task.findall("Action")
        codes = [int(a.find("code").text) for a in actions]
        assert CODE_IF in codes
        assert CODE_ELSE in codes
        assert CODE_END_IF in codes

    def test_continue_on_error_attribute(self):
        """continue_on_error serializes as <se>false</se> (Tasker's stop-on-error flag).

        Tasker's own export format uses <se>false</se>, not <con> — an
        unrecognized element is silently dropped on import and the flag
        never takes effect.
        """
        project = build_mvwifi_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        # HandlePortal's curl test action has continue_on_error=True
        tasks = root.findall("Task")
        handle_portal = next(t for t in tasks if t.find("nme").text == "HandlePortal")
        actions = handle_portal.findall("Action")
        se_actions = [a for a in actions if a.find("se") is not None]
        assert len(se_actions) > 0
        assert se_actions[0].find("se").text == "false"

    def test_project_tids(self):
        """Test that the project tids field lists all task IDs."""
        project = build_mvwifi_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        proj = root.find("Project")
        tids = proj.find("tids").text
        assert "40" in tids
        assert "50" in tids

    def test_custom_project(self):
        """Test generating XML for a custom minimal project."""
        task = TaskerTask(
            id=1,
            name="TestTask",
            actions=[flash("hello")],
        )
        project = TaskerProject(
            name="Test",
            profiles=[],
            tasks=[task],
        )
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        assert root.find("Project").find("name").text == "Test"
        assert root.find("Task").find("nme").text == "TestTask"

    def test_parses_with_xml_parser(self):
        """Test that the XML can be parsed by a strict XML parser."""
        project = build_mvwifi_project()
        xml_text = generate_project_xml(project)
        # parseString raises on malformed XML
        parseString(xml_text.encode("utf-8"))


class TestTermuxProject:
    """Test the Termux approach project builder."""

    def test_generates_valid_xml(self):
        """Test that the Termux project XML is well-formed."""
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        assert root.tag == "TaskerData"

    def test_parses_with_xml_parser(self):
        """Test that the Termux XML can be parsed by a strict parser."""
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        parseString(xml_text.encode("utf-8"))

    def test_project_name(self):
        """Test that the project name is MVwifiAuto-Termux."""
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        assert root.find("Project").find("name").text == "MVwifiAuto-Termux"

    def test_has_six_tasks(self):
        """Test that the Termux project has 6 tasks."""
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        tasks = root.findall("Task")
        assert len(tasks) == 8
        task_names = {t.find("nme").text for t in tasks}
        assert task_names == {
            "MVW_RunPortalScript",
            "MVW_PortalOnConnect",
            "MVW_CostcoConnect",
            "MVW_NudgeWifi",
            "MVW_NudgeXfinity",
            "MVW_ShowVersion",
            "MVW_SHGuestCapture",
            "MVW_UnlockNudge",
        }

    def test_profile_links_to_connect_and_run(self):
        """Test that the cmvwifi profile links to MVW_PortalOnConnect (id=81)."""
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        profiles = root.findall("Profile")
        profile = next(
            p for p in profiles if p.find("nme").text == "MVW cmvwifi Auto Connect"
        )
        assert profile.find("mid0").text == "81"

    def test_profile_uses_wifi_connected_state(self):
        """Termux profile triggers on WiFi Connected, not WiFi Near.

        WiFi Near polls scan results, which Android throttles to
        roughly one per 30 min for background apps — observed as a
        ~29-minute delay before portal handling. WiFi Connected
        (state code 160) fires on the association event itself.
        """
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        state = root.find("Profile").find("State")
        assert state.find("code").text == "160"
        # arg0 = SSID, arg3 = Active (2 = connected)
        assert state.find("Str").text == "cmvwifi"
        ints = state.findall("Int")
        assert ints[0].get("val") == "2"

    def test_run_portal_script_has_termux_plugin(self):
        """Test that MVW_RunPortalScript uses the Termux:Tasker plugin."""
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        tasks = root.findall("Task")
        run_script = next(t for t in tasks if t.find("nme").text == "MVW_RunPortalScript")
        actions = run_script.findall("Action")
        assert len(actions) == 1
        # The action should have code 1256900802 (Termux:Tasker plugin)
        assert actions[0].find("code").text == "1256900802"
        # The Bundle arg contains the plugin config as child elements
        bundles = actions[0].findall("Bundle")
        assert len(bundles) == 1
        # Serialize the bundle to check its content
        from xml.etree.ElementTree import tostring
        bundle_xml = tostring(bundles[0], encoding="unicode")
        assert "mvwifi_portal" in bundle_xml
        assert "com.termux.tasker.extra.EXECUTABLE" in bundle_xml

    def test_portal_on_connect_has_no_connect_action(self):
        """MVW_PortalOnConnect must NOT contain a Connect to WiFi action.

        The task only fires on WiFi Connected profiles — association is
        guaranteed. The old connect step was redundant, and hardcoding
        cmvwifi could pull the phone off the sibling MVwifi SSID (a
        ranking violation). Its %WIFII If-guards never matched anyway:
        Tasker's ~ op requires a whole-string match and %WIFII is
        multi-line (">>> SCAN <<<" / ">>> CONNECTION <<<").
        """
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        tasks = root.findall("Task")
        task = next(t for t in tasks if t.find("nme").text == "MVW_PortalOnConnect")
        codes = [int(a.find("code").text) for a in task.findall("Action")]
        assert CODE_CONNECT_WIFI not in codes
        assert CODE_WAIT not in codes
        # No SSID If-guards either — nothing in this task depends on
        # which municipal SSID triggered it.
        assert CODE_IF not in codes
        assert CODE_GOTO not in codes

    def test_portal_on_connect_uses_fresh_task_id(self):
        """MVW_PortalOnConnect gets its own id so a stale id-80
        MVW_ConnectAndRun orphan can't shadow it on merge-import."""
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        tasks = root.findall("Task")
        task = next(t for t in tasks if t.find("nme").text == "MVW_PortalOnConnect")
        assert task.get("sr") is not None
        assert task.find("id").text == "81"

    def test_portal_on_connect_has_perform_task(self):
        """MVW_PortalOnConnect calls MVW_RunPortalScript via Perform Task."""
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        tasks = root.findall("Task")
        task = next(t for t in tasks if t.find("nme").text == "MVW_PortalOnConnect")
        perform_actions = [
            a
            for a in task.findall("Action")
            if a.find("code").text == str(CODE_PERFORM_TASK)
        ]
        assert len(perform_actions) >= 1
        str_args = perform_actions[0].findall("Str")
        assert any(s.text == "MVW_RunPortalScript" for s in str_args)

    def test_portal_on_connect_has_termux_self_heal(self):
        """MVW_PortalOnConnect re-applies Termux protections via root shell."""
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        tasks = root.findall("Task")
        task = next(t for t in tasks if t.find("nme").text == "MVW_PortalOnConnect")
        shell_actions = [
            a
            for a in task.findall("Action")
            if a.find("code").text == str(CODE_RUN_SHELL)
        ]
        assert len(shell_actions) == 1
        shell = shell_actions[0]
        # Must continue on error so unrooted devices keep working
        assert shell.find("se") is not None and shell.find("se").text == "false"
        str_args = shell.findall("Str")
        assert any(
            "settings_enable_monitor_phantom_procs" in (s.text or "")
            for s in str_args
        )

    def test_portal_on_connect_action_order(self):
        """Marker, then self-heal, then the portal plugin — the self-heal
        must run before the plugin call that depends on Termux."""
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        tasks = root.findall("Task")
        task = next(t for t in tasks if t.find("nme").text == "MVW_PortalOnConnect")
        codes = [a.find("code").text for a in task.findall("Action")]
        assert codes == [
            str(CODE_WRITE_FILE),
            str(CODE_RUN_SHELL),
            str(CODE_PERFORM_TASK),
            str(CODE_FLASH),
        ]

    def test_mvwifi_profile(self):
        """MVW MVwifi Auto Connect profile triggers MVW_PortalOnConnect on the
        'MVwifi' SSID (same municipal network, alternate SSID)."""
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        profile = next(
            p
            for p in root.findall("Profile")
            if p.find("nme").text == "MVW MVwifi Auto Connect"
        )
        assert profile.find("mid0").text == "81"  # MVW_PortalOnConnect
        state = profile.find("State")
        assert state.find("code").text == "160"  # WiFi Connected
        assert state.find("Str").text == "MVwifi"

    def test_termux_task_builder(self):
        """Test the termux_task action builder."""
        action = termux_task("mvwifi_portal", background=True)
        assert action.code == CODE_TERMUX_TASK
        # arg0: Bundle with plugin config
        assert action.args[0].kind == "Bundle"
        assert "mvwifi_portal" in action.args[0].value
        assert "com.termux.tasker.extra.EXECUTABLE" in action.args[0].value
        assert "false" in action.args[0].value  # background=true -> TERMINAL=false
        # arg1: plugin package
        assert action.args[1].value == "com.termux.tasker"
        # arg2: config activity
        assert action.args[2].value == "com.termux.tasker.EditConfigurationActivity"
        # arg3: plugin action timeout in seconds (the Tasker UI
        # "Timeout" field reads/writes this arg)
        assert action.args[3].value == "60"
        # arg4: emitted by Tasker's normalization
        assert action.args[4].value == "0"

    def test_goto_builder(self):
        """Test the goto_action builder."""
        action = goto_action(6)
        # Pin to the literal value, not the symbolic constant:
        # asserting `action.code == CODE_GOTO` can't catch a wrong
        # constant — CODE_GOTO was once 731 (Take Call) and every
        # test passed. 135 = Goto per Tasker's action code table.
        assert action.code == 135
        assert action.code == CODE_GOTO
        # arg0: type selector — 0 = Action Number (an Int arg, not
        # a Str label; Tasker ignores mistyped args)
        assert action.args[0].value == "0"
        # arg1: target action number
        assert action.args[1].value == "6"
        # arg2: label (empty in number mode)
        assert action.args[2].value == ""

    def test_write_file_builder(self):
        """Test the write_file action builder (append mode)."""
        action = write_file("Tasker/mvwifi_history.log", "marker")
        assert action.code == CODE_WRITE_FILE
        assert action.args[0].value == "Tasker/mvwifi_history.log"
        assert action.args[1].value == "marker"
        assert action.args[2].value == "1"  # Append
        assert action.args[3].value == "1"  # Add Newline

    def test_connect_and_run_starts_with_history_marker(self):
        """MVW_PortalOnConnect must record a Tasker-side marker first, so a
        missing Termux-side entry can be distinguished from 'profile
        never fired'."""
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        tasks = root.findall("Task")
        task = next(
            t for t in tasks if t.find("nme").text == "MVW_PortalOnConnect"
        )
        first = task.findall("Action")[0]
        assert first.find("code").text == str(CODE_WRITE_FILE)
        assert "mvwifi_history.log" in first.find("Str").text

    def test_costco_profile(self):
        """MVW Costco WiFi Connected profile triggers MVW_CostcoConnect on the
        'Costco Member Wifi' SSID."""
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        profiles = root.findall("Profile")
        assert len(profiles) == 7
        costco = next(
            p for p in profiles if p.find("nme").text == "MVW Costco WiFi Connected"
        )
        assert costco.find("mid0").text == "90"  # MVW_CostcoConnect
        state = costco.find("State")
        assert state.find("code").text == "160"  # WiFi Connected
        assert state.find("Str").text == "Costco Member Wifi"

    def test_shguest_profile_and_task(self):
        """MVW SHGuestNet WiFi Connected → MVW_SHGuestCapture (id=130) runs the
        shguest_probe wrapper — capture only, never accepts."""
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        profiles = root.findall("Profile")
        shg = next(
            p
            for p in profiles
            if p.find("nme").text == "MVW SHGuestNet WiFi Connected"
        )
        assert shg.find("mid0").text == "130"  # MVW_SHGuestCapture
        state = shg.find("State")
        assert state.find("code").text == "160"  # WiFi Connected
        assert state.find("Str").text == "SHGuestNet"
        # Task runs the shguest_probe wrapper
        task = next(
            t
            for t in root.findall("Task")
            if t.find("nme").text == "MVW_SHGuestCapture"
        )
        from xml.etree.ElementTree import tostring

        assert "shguest_probe" in tostring(task, encoding="unicode")

    def test_unlock_profile_and_task(self):
        """MVW Display Unlocked Nudge → MVW_UnlockNudge (id=140) runs the
        throttled nudge on every screen unlock."""
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        profiles = root.findall("Profile")
        prof = next(
            p
            for p in profiles
            if p.find("nme").text == "MVW Display Unlocked Nudge"
        )
        assert prof.find("mid0").text == "140"  # MVW_UnlockNudge
        event = prof.find("Event")
        assert event is not None
        assert event.find("code").text == "1000"  # Display Unlocked
        task = next(
            t
            for t in root.findall("Task")
            if t.find("nme").text == "MVW_UnlockNudge"
        )
        from xml.etree.ElementTree import tostring

        body = tostring(task, encoding="unicode")
        assert "wifi_nudge" in body
        assert "--min-interval 300" in body

    def test_watchdog_project(self):
        """MVwifiWatchdog is a separate project: 6h Time profile →
        task that stats the history log and Notifies when stale."""
        from wifi_portal_automation.tasker_gen import build_watchdog_project

        project = build_watchdog_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        assert root.find("Project").find("name").text == "MVwifiWatchdog"
        profiles = root.findall("Profile")
        assert len(profiles) == 1
        prof = profiles[0]
        assert prof.find("nme").text == "MVW History Watchdog"
        assert prof.find("mid0").text == "200"
        tctx = prof.find("Time")
        assert tctx.find("rep").text == "1"  # hours
        assert tctx.find("repval").text == "1"  # hourly cadence
        tasks = root.findall("Task")
        assert len(tasks) == 1
        task = tasks[0]
        assert task.find("nme").text == "MVW_HistoryWatchdog"
        codes = [a.find("code").text for a in task.findall("Action")]
        assert codes == ["123", "37", "523", "548", "38"]
        from xml.etree.ElementTree import tostring

        body = tostring(task, encoding="unicode")
        assert "mvwifi_history.log" in body
        assert "86400" in body
        # Checks the termux-side nudge log too — Tasker markers alone
        # keep the history fresh when a dead Termux runs nothing.
        assert "mvwifi_nudge.log" in body
        assert "NAGE" in body
        # The termux check is RELATIVE: Tasker fresh + termux stale
        # >1h — a 24h absolute threshold can't catch a ~20h outage.
        assert "HAGE -lt 3600" in body
        assert "NAGE -gt 3600" in body
        # no_root streak check (root broken but logs stay fresh).
        assert "no_root" in body
        # Findings go to a separate log — writing them to the
        # monitored history log would refresh the mtime under test.
        assert "mvwifi_watchdog.log" in body
        # Tasker fails a Run Shell action on a nonzero exit: the
        # fresh-log path must still exit 0 (if/fi, not a trailing
        # bare test or &&-chain that returns 1).
        assert 'if [ -n "$DETAIL" ]' in body
        assert "echo STALE; fi" in body

    def test_costco_connect_task_runs_wrapper(self):
        """MVW_CostcoConnect calls the costco_portal Termux wrapper."""
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        tasks = root.findall("Task")
        task = next(t for t in tasks if t.find("nme").text == "MVW_CostcoConnect")
        actions = task.findall("Action")
        # A1: history marker Write File
        assert actions[0].find("code").text == str(CODE_WRITE_FILE)
        # A2: root self-heal Run Shell
        assert actions[1].find("code").text == str(CODE_RUN_SHELL)
        # A3: Termux plugin invoking costco_portal
        assert actions[2].find("code").text == str(CODE_TERMUX_TASK)
        bundle_xml = tostring(
            actions[2].find("Bundle"), encoding="unicode"
        )
        assert "costco_portal" in bundle_xml

    def test_nudge_profile_uses_time_context(self):
        """MVW cmvwifi Periodic Nudge fires every 15 min via a Time context.

        <Time> serializes as fh/fm/th/tm (-1 = unset, whole day) plus
        rep/repval for the repeat (unit 2 = minutes) — layout verified
        against real Tasker exports.  Android's screen-off PNO scans
        are throttled and its network selector backs off cmvwifi, so
        a periodic nudge covers the gap WiFi Near/Display On leave.
        """
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        profile = next(
            p
            for p in root.findall("Profile")
            if p.find("nme").text == "MVW cmvwifi Periodic Nudge"
        )
        assert profile.find("mid0").text == "100"  # MVW_NudgeWifi
        assert profile.find("State") is None
        time = profile.find("Time")
        assert time is not None
        assert time.find("fh").text == "-1"
        assert time.find("fm").text == "-1"
        assert time.find("th").text == "-1"
        assert time.find("tm").text == "-1"
        assert time.find("rep").text == "2"  # repeat unit: minutes
        assert time.find("repval").text == "15"

    def test_nudge_task_runs_wrapper(self):
        """MVW_NudgeWifi: marker, self-heal/resurrect, then wifi_nudge
        with the cmvwifi preferred + xfinitywifi fallback arguments."""
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        task = next(
            t
            for t in root.findall("Task")
            if t.find("nme").text == "MVW_NudgeWifi"
        )
        actions = task.findall("Action")
        # A1: history marker Write File
        assert actions[0].find("code").text == str(CODE_WRITE_FILE)
        assert "mvwifi_history.log" in actions[0].find("Str").text
        # A2: root self-heal — protections + resurrect a dead Termux
        # (plugin calls report OK even when Termux never runs)
        assert actions[1].find("code").text == str(CODE_RUN_SHELL)
        shell_body = tostring(actions[1], encoding="unicode")
        assert "pidof com.termux" in shell_body
        # A3: Termux plugin invoking wifi_nudge
        assert actions[2].find("code").text == str(CODE_TERMUX_TASK)
        bundle_xml = tostring(
            actions[2].find("Bundle"), encoding="unicode"
        )
        assert "wifi_nudge" in bundle_xml
        assert "--preferred" in bundle_xml
        assert "--fallback xfinitywifi" in bundle_xml

    def test_xfinity_nudge_task(self):
        """MVW_NudgeXfinity calls wifi_nudge with xfinitywifi + -d."""
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        task = next(
            t for t in root.findall("Task") if t.find("nme").text == "MVW_NudgeXfinity"
        )
        actions = task.findall("Action")
        assert actions[0].find("code").text == str(CODE_WRITE_FILE)
        assert actions[1].find("code").text == str(CODE_RUN_SHELL)
        assert actions[2].find("code").text == str(CODE_TERMUX_TASK)
        bundle_xml = tostring(
            actions[2].find("Bundle"), encoding="unicode"
        )
        assert "wifi_nudge" in bundle_xml
        assert "--ssid xfinitywifi" in bundle_xml
        assert "--autojoin-disabled" in bundle_xml
        assert "--defer-to-preferred" in bundle_xml

    def test_xfinity_nudge_profile(self):
        """MVW xfinitywifi Periodic Nudge: Time context every 15 min ->
        MVW_NudgeXfinity (id=110), toggleable to exclude xfinitywifi."""
        project = build_termux_project()
        xml_text = generate_project_xml(project)
        root = fromstring(xml_text)
        profile = next(
            p
            for p in root.findall("Profile")
            if p.find("nme").text == "MVW xfinitywifi Periodic Nudge"
        )
        assert profile.find("mid0").text == "110"
        time = profile.find("Time")
        assert time is not None
        assert time.find("rep").text == "2"
        assert time.find("repval").text == "15"


class TestActionLabels:
    """Action labels — Tasker's closest equivalent to inline comments."""

    def test_label_serializes(self):
        """A labeled action emits a <label> child inside <Action>."""
        project = TaskerProject(
            name="T",
            profiles=[],
            tasks=[
                TaskerTask(
                    id=1,
                    name="t",
                    actions=[TaskerAction(code=CODE_STOP, label="stop here")],
                )
            ],
        )
        root = fromstring(generate_project_xml(project))
        action = root.find("Task/Action")
        assert action.find("label").text == "stop here"

    def test_unlabeled_action_has_no_label(self):
        """Actions without a label emit no <label> element."""
        project = TaskerProject(
            name="T",
            profiles=[],
            tasks=[
                TaskerTask(
                    id=1,
                    name="t",
                    actions=[TaskerAction(code=CODE_STOP)],
                )
            ],
        )
        root = fromstring(generate_project_xml(project))
        assert root.find("Task/Action/label") is None

    def test_termux_plugin_actions_are_labeled(self):
        """Every Termux:Tasker plugin call in the project has a label —
        the plugin action code (1256900802) is opaque in the Tasker UI."""
        root = fromstring(generate_project_xml(build_termux_project()))
        for task in root.findall("Task"):
            for action in task.findall("Action"):
                if action.find("code").text == str(CODE_TERMUX_TASK):
                    label = action.find("label")
                    assert label is not None and label.text
                    assert ".termux/tasker/" in label.text


class TestGenerationStamp:
    """The gen placeholder is replaced by a content-hash stamp."""

    def test_markers_carry_gen_id(self):
        """Every task marker line ends with [gen xxxxxx]."""
        import re

        xml_text = generate_project_xml(build_termux_project())
        root = fromstring(xml_text)
        gens = set()
        for task in root.findall("Task"):
            for arg in task.iter("Str"):
                text = arg.get("val") or (arg.text or "")
                if "| tasker |" in text:
                    m = re.search(r"\[gen ([0-9a-f]{6})\]$", text)
                    assert m is not None, text
                    gens.add(m.group(1))
        # All markers share one generation id
        assert len(gens) == 1
        # Placeholder never survives into the output
        assert "@MWGEN@" not in xml_text

    def test_gen_is_deterministic(self):
        """Identical project content produces an identical stamp."""
        xml1 = generate_project_xml(build_termux_project())
        xml2 = generate_project_xml(build_termux_project())
        assert xml1 == xml2

    def test_gen_changes_with_content(self):
        """Changing project content changes the stamp."""
        from dataclasses import replace as dc_replace

        p1 = build_termux_project()
        p2 = build_termux_project()
        p2.tasks = [
            dc_replace(t, name=f"{t.name}X") if t.name == "MVW_ShowVersion" else t
            for t in p2.tasks
        ]
        assert generate_project_xml(p1) != generate_project_xml(p2)

    def test_show_version_flashes_gen(self):
        """The MVW_ShowVersion task flashes the same gen id."""
        xml_text = generate_project_xml(build_termux_project())
        root = fromstring(xml_text)
        task = next(t for t in root.findall("Task") if t.find("nme").text == "MVW_ShowVersion")
        strs = list(task.iter("Str"))
        assert any("MVwifiAuto-Termux gen " in (a.get("val") or a.text or "") for a in strs)


class TestCollisionSemantics:
    """<rty> is task collision handling, NOT priority — Tasker reads
    0=Abort New Task, 1=Abort Existing Task, 2=Run Both Together.
    Same bug class as <con>: plausible XML meaning something else."""

    def test_all_tasks_use_abort_new(self):
        """Every task deliberately serializes Abort New — a duplicate
        tick firing while a run is in flight must be dropped, not
        run concurrently (Run Both was the old accidental value)."""
        for project in (
            build_termux_project(),
            build_watchdog_project(),
            build_mvwifi_project(),
        ):
            for task in project.tasks:
                assert task.collision == COLLISION_ABORT_NEW, task.name

    def test_rty_serializes_collision(self):
        root = fromstring(generate_project_xml(build_termux_project()))
        for task in root.findall("Task"):
            assert int(task.find("rty").text) == COLLISION_ABORT_NEW


class TestBundleSerialization:
    """Plugin bundle values must round-trip through XML escaping —
    unescaped interpolation produced malformed XML that Tasker
    imported as a plugin action with no configuration (D6)."""

    def test_special_chars_escaped(self):
        action = termux_task(executable="probe", arguments="a & b < c")
        bundle_root = fromstring(action.args[0].value)
        args_elem = bundle_root.find("com.termux.execute.arguments")
        assert args_elem.text == "a & b < c"
        blurb = bundle_root.find(
            "com.twofortyfouram.locale.intent.extra.BLURB"
        )
        assert blurb.text == "probe a & b < c"

    def test_bundle_structure_preserved(self):
        """Generated bundle still matches the Termux:Tasker template."""
        action = termux_task(executable="mvwifi_portal", arguments="--once")
        bundle_root = fromstring(action.args[0].value)
        assert bundle_root.tag == "Vals"
        assert bundle_root.get("sr") == "val"
        for key in (
            "com.termux.execute.arguments",
            "com.termux.tasker.extra.EXECUTABLE",
            "com.termux.tasker.extra.TERMINAL",
            "com.termux.tasker.extra.VERSION_CODE",
            "net.dinglisch.android.tasker.subbundled",
        ):
            assert bundle_root.find(key) is not None, key
            assert bundle_root.find(f"{key}-type") is not None

    def test_malformed_bundle_raises(self):
        """An unparseable bundle raises instead of silently degrading
        to escaped text (which imports as a config-less action)."""
        with pytest.raises(ParseError):
            generate_project_xml(
                TaskerProject(
                    name="P",
                    profiles=[],
                    tasks=[
                        TaskerTask(
                            id=1,
                            name="T",
                            actions=[
                                TaskerAction(
                                    code=1,
                                    args=[TaskerArg.bundle_arg(0, "<broken")],
                                )
                            ],
                        )
                    ],
                )
            )


class TestCommittedXmlIsCurrent:
    """Every committed .prj.xml must equal generator output — this is
    how the stale <con>-tagged legacy XML went unnoticed (D7)."""

    @pytest.mark.parametrize(
        "path,builder",
        [
            ("android/MVwifiAuto.prj.xml", build_mvwifi_project),
            ("android/MVwifiAuto-Termux.prj.xml", build_termux_project),
            ("android/MVwifiWatchdog.prj.xml", build_watchdog_project),
        ],
    )
    def test_committed_xml_matches_generator(self, path, builder):
        from pathlib import Path

        committed = (Path(__file__).resolve().parents[1] / path).read_text()
        assert committed == generate_project_xml(builder()), (
            f"{path} is stale — regenerate via tasker_gen"
        )


class TestTaskerInvariants:
    """Structural invariants the generator must uphold."""

    def test_plugin_calls_follow_self_heal(self):
        """Every Termux:Tasker plugin action at index >0 in its task
        is preceded by the self-heal pidof/startservice shell.  The
        exception is MVW_RunPortalScript, whose plugin call is first
        by design — its only caller self-heals before invoking it."""
        root = fromstring(generate_project_xml(build_termux_project()))
        for task in root.findall("Task"):
            name = task.find("nme").text
            actions = task.findall("Action")
            for i, action in enumerate(actions):
                if action.find("code").text != str(CODE_TERMUX_TASK):
                    continue
                prior_strs = "".join(
                    s.text or ""
                    for a in actions[:i]
                    for s in a.iter("Str")
                )
                if i == 0:
                    assert name == "MVW_RunPortalScript", (
                        f"{name} plugin call at index 0 must be the "
                        "dedicated RunPortalScript task"
                    )
                else:
                    assert "pidof com.termux" in prior_strs, (
                        f"{name} action {i}: plugin call not preceded "
                        "by the Termux self-heal"
                    )

    def test_wifii_comparisons_are_contains_style(self):
        """No Condition may compare %WIFII with a bare SSID — the ~
        op is a whole-string match and %WIFII is a multi-line blob
        (>>> SCAN <<< / >>> CONNECTION <<<).  RHS must be globbed."""
        for builder in (
            build_termux_project,
            build_mvwifi_project,
            build_watchdog_project,
        ):
            root = fromstring(generate_project_xml(builder()))
            for cond in root.iter("Condition"):
                lhs = cond.find("lhs").text or ""
                rhs = cond.find("rhs").text or ""
                if "%WIFII" in lhs:
                    assert "*" in rhs, (
                        f"{lhs} ~ {rhs}: whole-string match can never "
                        "succeed against multi-line %WIFII"
                    )
