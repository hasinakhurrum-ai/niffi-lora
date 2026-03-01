import random

def evaluate():
    # Generate a random float between 0 and 100
    score = random.uniform(0, 100)
    return score

# Call the function and print the result
print(evaluate())