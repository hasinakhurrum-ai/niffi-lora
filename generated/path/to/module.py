import subprocess

def safe_system_call(*args):
    # Validate inputs: ensure all are strings or integers
    for arg in args:
        if not isinstance(arg, (str, int)):
            raise TypeError(f"Invalid argument type {type(arg)}. Expected str or int.")

    # Convert arguments to strings and join them into a single command string
    cmd = ' '.join(map(str, args))

    # Execute the command
    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Shell call failed with error: {e}")