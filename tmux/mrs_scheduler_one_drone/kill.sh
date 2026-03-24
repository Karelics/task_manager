#!/bin/bash

# Absolute path to this script. /home/user/bin/foo.sh
SCRIPT=$(readlink -f $0)
# Absolute path this script is in. /home/user/bin
SCRIPTPATH=`dirname $SCRIPT`
cd "$SCRIPTPATH"

export TMUX_SESSION_NAME=simulation
export TMUX_SOCKET_NAME=mrs

# just attach to the session
tmux -L $TMUX_SOCKET_NAME split-window -t $TMUX_SESSION_NAME
tmux -L $TMUX_SOCKET_NAME send-keys -t $TMUX_SESSION_NAME "sleep 1; tmux list-panes -s -F \"#{pane_pid} #{pane_current_command}\" | grep -v tmux | cut -d\" \" -f1 | while read in; do killProcessRecursive \$in; done; exit" ENTER
