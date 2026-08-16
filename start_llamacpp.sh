 llama serve   -hf PaddlePaddle/PaddleOCR-VL-1.6-GGUF   --host 0.0.0.0 --port 8080   \
--n-gpu-layers 99   --ctx-size 8192   --parallel 4   --cont-batching   --flash-attn on  \
--threads 8
