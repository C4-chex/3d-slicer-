# NV-Reason-CT 技术学习笔记

阅读范围：本地 `Nvidia ct/NV-Reason-CT` 仓库的 README、推理入口、训练入口、配置、标签/奖励实现、安全政策、许可证和第三方声明。此次只读学习；没有安装依赖、下载模型权重、执行推理或修改参考仓库。

## 结论

NV-Reason-CT 是胸部/腹部 CT 的 3D 视觉语言模型示例，提供结构化报告生成、影像问答和文本推理。它不是器官分割工具，不输出可信分割掩膜，也不生成 STL/OBJ。对 CT3D 的合适定位是**核心阅片/三维浏览之外的可选 AI 文本建议模块**。

## 模型与输入流水线

- 模型组成：Qwen3.5-4B 语言模型 + Primus 3D Vision Transformer（以 COLIPRI 权重初始化）。
- 区域范围：`chest` 或 `abdomen`；输入必须是数值已正确转换的 CT HU 体数据。仓库不面向 MR。
- 推理入口 `inference.py` 只接收单个 `.nii` / `.nii.gz` 路径；用 Transformers `AutoModelForImageTextToText` 与 `AutoProcessor`，通过 `images3d=` 传入体数据，生成文本。
- 模型包和自定义 3D Processor 代码不在 Git 仓库；README 指向 Hugging Face 上的模型包。首次使用需取得模型包/权重。
- README 描述 Processor 的路径：LPS 朝向、2 mm 等方重采样、按胸部或腹部执行解剖区域裁剪，最终使用 192×192×192 体素。以 8×8×8 非重叠 patch 输入，视觉网格为 24×24×24，即 13,824 个视觉 token。
- 重要几何限制：README 的 crop 检查例子指出，Processor 的 `load_image()` 不保留源扫描的世界坐标原点。若模型提到某个 finding，不能仅凭 crop 输出把它当成已映射回原 DICOM 的坐标。

## 推理和训练代码边界

- 默认命令要求 CUDA GPU，模型以 BF16 加载，默认启用 thinking，默认 `max_new_tokens=2048`。这不是适合直接塞进 Slicer Python 环境的轻量依赖。
- `trust_remote_code=True` 会执行所选 Hugging Face 模型包中的自定义 Python 代码。若后续部署，应固定并验证模型 revision，隔离推理环境；不能让核心导入/阅片依赖它。
- 推理结果为模型生成文本。仓库没有把结果变成像素级分割、DICOM SEG、病灶坐标或表面网格的实现。
- SFT/GRPO 脚本是训练示例，不是普通本机功能：基础依赖包括 CUDA PyTorch、Transformers、MONAI、Nibabel 等；配置面向单机 8 张 NVIDIA GPU。训练数据清单有 128 个 CT-RATE 示例记录，但仓库不包含对应训练 CT 体数据。
- GRPO 奖励包括异常标签集合 F1、报告结构评分和长度惩罚；这些是训练格式奖励，不证明临床准确性。GRPO trainer 为处理 5D 体素张量和 `images3d=` 改写/扩展了 TRL 逻辑，且关闭 vLLM 路径。

## 对 CT3D 后续集成的设计约束

1. 先由 CT3D 完成 DICOM 序列选择、几何检查、像素到 HU 变换和体数据载入；AI 不参与排序、补片、方向修复或 HU 推断。
2. 将 DICOM 转 NIfTI/HU 作为独立、可记录的适配层；记录输入 Series、方向、间距、重采样、crop 范围和模型输入覆盖范围。保留从模型 crop 回到源体素/患者坐标的明确变换；没有可靠变换时只展示文字，不显示伪精确定位。
3. AI 作为独立可选模块/进程运行。没有模型、CUDA、网络或 AI 依赖时，核心 DICOM 浏览、MPR、体渲染、手动分割和 STL 导出仍应可用。
4. 将输出标成模型建议，并记录模型仓库/revision、Processor 配置、区域、提示词、推理参数和时间；允许专业人员查看、编辑或忽略。不可将报告文字直接转成自动分割或诊断。
5. 默认本机处理。不要把 DICOM、NIfTI、prompt 或生成报告发往远端服务，除非用户明确发起并清楚看到数据去向。
6. 为适配模型单独管理 Python/CUDA 环境，避免安装 Transformers 5.x、Torch/CUDA 或 `trust_remote_code` 影响 Slicer 运行时。

## 权重、依赖与许可观察

- Git 仓库含有三个已实际取回的 LFS NIfTI 示例，但 README 说明完整权重和自定义模型代码另由 Hugging Face 模型仓库维护。
- 项目使用 OpenMDW-1.1，而不是默认可按 MIT/Apache 处理。许可证要求分发模型材料时保留许可证和来源/版权声明，并要求使用者自行确认其他权利。打包或分发模型前需要单独做许可审查。
- `SECURITY.md` 将 remote code、临床影像及输出报告/训练 checkpoint 的暴露列为主要风险；项目没有服务端鉴权或数据沙箱。
- 项目自身声明仅用于研究与开发，不是医疗器械，不可替代专业临床判断、诊断或治疗。

## 主要参考文件

- `Nvidia ct/NV-Reason-CT/README.md`
- `Nvidia ct/NV-Reason-CT/inference.py`
- `Nvidia ct/NV-Reason-CT/train/vlm_sft_train.py`
- `Nvidia ct/NV-Reason-CT/train/vlm_grpo_train.py`
- `Nvidia ct/NV-Reason-CT/train/vlm_grpo_trainer.py`
- `Nvidia ct/NV-Reason-CT/train/vlm_labels.py`
- `Nvidia ct/NV-Reason-CT/train/vlm_rewards.py`
- `Nvidia ct/NV-Reason-CT/configs/sft_config.yaml`
- `Nvidia ct/NV-Reason-CT/configs/grpo_config.yaml`
- `Nvidia ct/NV-Reason-CT/SECURITY.md`
- `Nvidia ct/NV-Reason-CT/LICENSE`
- `Nvidia ct/NV-Reason-CT/THIRD-PARTY-NOTICES`
