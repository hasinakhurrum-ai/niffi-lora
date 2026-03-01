import subprocess

def validate_command(command):
    try:
        # Validate input arguments
        if len(command.split()) != 2:
            raise ValueError("Invalid command format")
        
        # Check if the interpreter exists
        subprocess.run(['python', '--version'], check=True)
        return True
    
    except Exception as e:
        print(f"Error validating command: {e}")
        return False

def run_command(command):
    try:
        # Run the shell command
        output = subprocess.check_output(command, shell=True).decode('utf-8')
        return output
    
    except subprocess.CalledProcessError as e:
        print(f"Command failed with exit code {e.returncode}: {e.output}")
        return None

def validate_python_interpreter():
    try:
        # Run the python interpreter
        subprocess.run(['python', '--version'], check=True)
    
    except Exception as e:
        print(f"Error validating Python interpreter: {e}")

def main():
    while True:
        command = input("Enter a command (or 'q' to quit): ")
        
        if command.lower() == 'q':
            break
        
        validate_command(command)
        output = run_command(command)
        print(output)