import subprocess
from typing import List, Dict

class Sandbox:
    def run_shell(self, command: str, args: List[str] = [], **kwargs) -> None:
        try:
            # Validate input arguments
            if not isinstance(command, str):
                raise ValueError("Command must be a string")
            if not all(isinstance(arg, str) for arg in args):
                raise ValueError("All arguments must be strings")

            # Use subprocess.run with error handling
            result = subprocess.run([command] + args, **kwargs)
            result.check_returncode()  # Raise an exception on non-zero exit status
        except (ValueError, TypeError, subprocess.CalledProcessError) as e:
            print(f"An error occurred: {e}")