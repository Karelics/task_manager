# Enumeration for task status

string RECEIVED="RECEIVED"          # Task was received and is waiting to be executed
string IN_PROGRESS="IN_PROGRESS"    # Task is being executed
string DONE="DONE"                  # Task has finished successfully
string ERROR="ERROR"                # Task execution failed or was aborted by the server
string CANCELED="CANCELED"          # Task was canceled by the user or by another conflicting task
