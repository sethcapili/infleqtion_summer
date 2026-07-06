from transformers import pipeline

# Example: text generation with GPT-2
generator = pipeline("text-generation", model="gpt2")
result = generator("Once upon a time", max_length=50)
print(result)