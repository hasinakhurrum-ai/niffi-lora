import json
from datetime import datetime

class TaskAnalytics:
    def __init__(self):
        self.task_data = []

    def log_task(self, task_id, state, start_time=None, end_time=None):
        if not start_time:
            start_time = datetime.now()
        if not end_time:
            end_time = datetime.now()

        task_duration = (end_time - start_time).total_seconds()
        entry = {
            "task_id": task_id,
            "state": state,
            "start_time": str(start_time),
            "end_time": str(end_time),
            "duration": task_duration
        }
        self.task_data.append(entry)

    def save_to_file(self, file_path):
        with open(file_path, 'w') as f:
            json.dump(self.task_data, f, indent=4)

# Initialize the task analytics logger
task_analytics_logger = TaskAnalytics()

# Example usage in the scheduler
def run_task(task_id):
    start_time = datetime.now()
    try:
        # Simulate some work
        task_result = "Task completed successfully"
        end_time = datetime.now()
        task_analytics_logger.log_task(task_id, 'success', start_time, end_time)
    except Exception as e:
        end_time = datetime.now()
        task_analytics_logger.log_task(task_id, 'failure', start_time, end_time)
        # Log the exception
        print(f"Task {task_id} failed: {str(e)}")
    finally:
        task_analytics_logger.save_to_file('task_analytics.json')