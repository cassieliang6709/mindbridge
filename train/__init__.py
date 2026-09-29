"""Stage two: fine-tune and evaluate the local extractor.

prepare_dataset.py  split the captured pairs by date into train/holdout
train_qlora.py       Unsloth QLoRA on a rented CUDA GPU (not this Mac)
eval_holdout.py      measure schema compliance and cost, then optionally publish

中文说明：第二阶段——微调并评估本地抽取模型。``prepare_dataset.py`` 按日期将
采集的数据对拆成训练集/留出集(holdout);``train_qlora.py`` 在租用的 CUDA GPU
上运行 Unsloth QLoRA(不在本机 Mac);``eval_holdout.py`` 计算 schema 合规率与
成本,并且只在明确授权时发布结果。
"""
