"""M2 extraction of prose diaries and durable preferences.

Nothing here fine-tunes anything. It can call an explicitly authorized hosted
provider or the local MLX adapter, produces the diary the UI shows, and writes
the (prompt, JSON) pairs used to train and evaluate Qwen2.5-3B.
"""
