"""M2 extraction of prose diaries and durable preferences.

Nothing here fine-tunes anything. It can call an explicitly authorized hosted
provider or the local MLX adapter, produces the diary the UI shows, and writes
the (prompt, JSON) pairs used to train and evaluate Qwen2.5-3B.

中文说明：本模块负责 M2 阶段的抽取工作——把一天的对话转写为散文体日记和可长期
保留的偏好。这里不做任何模型微调；它可以调用经过明确授权的托管服务商，也可以
调用本地 MLX 适配器，生成 UI 展示的日记，并写出用于训练和评估 Qwen2.5-3B 的
(prompt, JSON) 样本对。
"""
