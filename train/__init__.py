"""Stage two: fine-tune and evaluate the local extractor.

prepare_dataset.py  split the captured pairs by date into train/holdout
train_qlora.py       Unsloth QLoRA on a rented CUDA GPU (not this Mac)
eval_holdout.py      measure schema compliance and cost, then optionally publish
"""
