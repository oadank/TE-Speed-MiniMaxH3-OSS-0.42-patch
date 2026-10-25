# TE-Speed-MiniMaxH3-OSS · ComfyUI 0.42 适配补丁

[TE-Speed-MiniMaxH3-OSS](https://github.com/HELPMEEADICE/TE-Speed-MiniMaxH3-OSS) 是闭源节点
[tl2012tl/TE-Speed-MiniMaxH3](https://github.com/tl2012tl/TE-Speed-MiniMaxH3)（只发 Windows 的
nodes.pyd，Linux 无法加载）的开源净室重实现。它需要先给 ComfyUI 核心的
`comfy/ldm/minimax/model.py` 打 `block_loop` 钩子补丁，但**官方自带的
`patch_model.py` 在 ComfyUI 0.42 上直接失败**：

```
error: could not find the stock block loop in the model file.
This ComfyUI version may differ; report it or patch manually.
```

原因：ComfyUI 0.42 重写了 MiniMax H3 的块循环——prefetch API 带
`malloc_scope`、新增 `transformer_options["block_index"]`、`attention` 透传、
`layout` 进入 `double_block` 钩子参数——官方补丁的锚点正则全部失配
（0.42 太新，上游尚未适配）。

本仓库提供适配 0.42 的补丁脚本 `patch_model_042.py`。

## 设计原则（为什么不手改 model.py）

- 从目标 `model.py` **现场提取**循环原文，锚点逐字校验（`# blocks` 注释起，
  末行 `prefetch_queue_pop(..., None, malloc_scope="block")` 止）；
  不唯一/找不到立即中止，**不写任何内容**。
- 补丁后的 `else` 分支与 `_run_blocks` 方法体由**校验过的原文程序化重排**生成，
  仅做 4 处机械替换：块区间切片（`blocks[start:end]`）、`device → h.device`、
  全局 `block_index`、`layout` 透传——**零手抄代码**，杜绝版本漂移时抄错。
- 钩子为运行时 opt-in：只有工作流里挂了 `TESpeedMiniMaxH3` 节点才走新分支，
  普通 H3 工作流行为与原版完全一致。
- 自带 `.bak` 备份 + `--revert` 一键回退 + `ast.parse` 语法门。

## 用法

```bash
# 1. 先装 OSS 节点本体（custom_nodes）
git clone https://github.com/HELPMEEADICE/TE-Speed-MiniMaxH3-OSS.git \
    ComfyUI/custom_nodes/TE-Speed-MiniMaxH3-OSS

# 2. 用本补丁替代官方 patch_model.py（在 OSS 目录里跑）
python patch_model_042.py --comfy-ui /opt/ComfyUI   # 省略 --comfy-ui 则自动探测

# 3. 重启 ComfyUI，工作流里接 TESpeedMiniMaxH3 节点即可

# 其他
python patch_model_042.py --check    # 只看状态
python patch_model_042.py --revert   # 从 .bak 恢复
```

> 提示：旧工作流里 TESpeedMiniMaxH3 节点多出的 `mode: "4-step LoRA"` 遗留
> widget 会被 ComfyUI 当未定义输入自动忽略，**无需改工作流**。

## 实测环境与效果

| 项 | 值 |
|---|---|
| 系统 | Debian 13 / Python 3.13.5 |
| ComfyUI | 0.42（`comfy/ldm/minimax/model.py` 41140 字节版） |
| torch | 2.8.0+cu128（RTX 3080, sm86） |
| 模型 | MiniMax H3 fl2va int8 + lightx2v turbo 4-step LoRA，3 秒 544×960 视频 |

同一 workflow 仅切换 `mcs`（0=关缓存 / 2=开缓存），模型热态背靠背三跑：

| 跑次 | mcs | 耗时 |
|---|---|---|
| 热态基线 | 0 | 165.6 s |
| 热态缓存 | 2 | **115.2 s（-30.4%）** |

（总耗时含 RIFE 插帧/放大/编码固定开销；采样段提速与上游宣称的 ~45% 吻合。）

## 致谢与许可

- [HELPMEEADICE/TE-Speed-MiniMaxH3-OSS](https://github.com/HELPMEEADICE/TE-Speed-MiniMaxH3-OSS)：
  节点本体与官方补丁的思路（本补丁在其 `patch_model.py` 基础上适配 0.42）
- [tl2012tl/TE-Speed-MiniMaxH3](https://github.com/tl2012tl/TE-Speed-MiniMaxH3)：原版闭源节点

本仓库补丁脚本以 MIT 许可发布（见 [LICENSE](LICENSE)）。
