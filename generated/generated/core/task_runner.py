import json

class TaskRunner:
    def __init__(self, bot_name, run_id):
        self.bot_name = bot_name
        self.run_id = run_id

    def execute_task(self, task_type, args=None):
        # Validate input parameters
        if not isinstance(task_type, str) or not task_type:
            raise ValueError("Task type must be a non-empty string")

        try:
            # Execute the task
            if task_type == "run_candidate":
                self.run_candidate(args)
            else:
                raise NotImplementedError(f"Task type '{task_type}' is not supported")
        
        except Exception as e:
            print(f"Error executing task {task_type}: {e}")
            raise

    def run_candidate(self, args):
        # Validate input arguments
        if not isinstance(args, dict) or not args:
            raise ValueError("Arguments must be a non-empty dictionary")

        try:
            # Check for required parameters
            if "model_name" not in args or not args["model_name"]:
                raise KeyError("Model name is missing in task arguments")

            # Log detailed information about the task execution
            print(f"Executing run_candidate with model: {args['model_name']}")

            # Simulate task execution (replace with actual logic)
            result = f"Task executed for model: {args['model_name']}"
            print(result)

        except Exception as e:
            raise

# Example usage
if __name__ == "__main__":
    runner = TaskRunner("bot1", "run1")
    try:
        runner.execute_task("run_candidate", {"model_name": "example_model"})
    except Exception as e:
        print(f"Execution failed: {e}")