# 历史遗留：llama.cpp + PaddleOCR-VL-1.6-GGUF 曾因参数/并行数不匹配
# 出现偶发 FAILED 页；当前推荐 vllm-server（见 AGENTS.md “OCR 后端”）。
# 仅保留此脚本作为参考，当前环境请改用 vllm-server 启动。
 llama serve   -hf PaddlePaddle/PaddleOCR-VL-1.6-GGUF   --host 0.0.0.0 --port 8080   \
--n-gpu-layers 99   --ctx-size 8192   --parallel 4   --cont-batching   --flash-attn on  \
--threads 8
