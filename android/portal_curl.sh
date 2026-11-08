#!/data/data/com.termux/files/usr/bin/bash
export LD_LIBRARY_PATH=/data/data/com.termux/files/usr/lib
export PATH=/data/data/com.termux/files/usr/bin:$PATH
curl "$@"
