# HSPICE Tools 🛠️

HSPICE 仿真数据解析与可视化工具集，提供从仿真输出到数据分析的完整工作流。

## 工具概览

| 文件 | 功能 |
|------|------|
| `HSPICE_READER_2.py` | 解析 HSPICE `.lis` 文件，提取瞬态仿真数据、`.MEASURE` 测量结果和直流工作点，输出 CSV / NPZ / JSON |
| `PLOT.py` | 基于 `HSPICE_READER_2` 输出的数据文件，快速绘制 X‑Y 曲线图，支持双纵轴 |
| `RUN_and_PLOT.py` | 一键完成 HSPICE 仿真 → 数据解析 → 绘图全流程 |

## 环境要求

- Python 3.8+
- NumPy
- Matplotlib
- HSPICE（仅 `RUN_and_PLOT.py` 需要）

```bash
pip install numpy matplotlib
```

---

### 📄 HSPICE_READER_2.py — .lis 文件解析器

从 HSPICE 输出的 `.lis` 文件中提取波形数据与测量结果。

#### 使用方式

```bash
# 使用脚本内 CONFIG 中的配置
python HSPICE_READER_2.py

# 命令行指定 .lis 文件路径
python HSPICE_READER_2.py simulation.lis

# 仅打印摘要，不保存文件
python HSPICE_READER_2.py --no-save

# 指定输出目录
python HSPICE_READER_2.py -o ./results
```

#### 输出文件

| 文件 | 格式 | 说明 |
|------|------|------|
| `<basename>_data.csv` | CSV | 通用表格，可导入 Origin / Excel |
| `<basename>_data.npz` | NPZ | NumPy 压缩存档，Python 快速加载 |
| `<basename>_info.json` | JSON | `.MEASURE` 结果 + 直流工作点 + 变量元信息 |

#### Python 加载 NPZ 示例

```python
import numpy as np
d = np.load('xxx_data.npz')
d['time']       # (N,)       时间轴（秒）
d['data']       # (N, M)     数据矩阵
d['var_names']  # (M,)       变量名列表
d['var_types']  # (M,)       类型：'voltage' 或 'current'
```

---

### 📈 PLOT.py — 数据可视化

从 `HSPICE_READER_2.py` 生成的 CSV / NPZ 文件中选取任意变量绘制 X‑Y 曲线。

#### 使用方式

```bash
# 直接运行（使用脚本内 CONFIG）
python PLOT.py

# 指定坐标轴变量
python PLOT.py -x time -y "V1,R (Ω)"

# 仅列出数据文件中所有可用变量
python PLOT.py -l
```

#### 特性

- 支持 `.npz` 和 `.csv` 格式
- 变量名模糊匹配（`v1` 可匹配 `V1`）
- 双纵轴：第一条曲线在左轴，后续自动分配右轴
- 自定义图标题、尺寸、分辨率、线宽

---

### 🔄 RUN_and_PLOT.py — 一键仿真管线

自动完成 **HSPICE 仿真 → 数据解析 → 绘图** 全流程。

#### 执行流程

1. 调用 HSPICE 对 `.sp` 文件进行仿真
2. 解析生成的 `.lis` 文件 → 保存 CSV / NPZ / JSON 到 `data/`
3. 绘图 → 保存 PNG 到 `figures/`

#### 使用方式

```bash
# 直接运行（使用脚本内 CONFIG）
python RUN_and_PLOT.py

# 命令行指定 .sp 文件
python RUN_and_PLOT.py simulation.sp
```

#### 前置条件

- HSPICE 已安装且在 `PATH` 中
- `HSPICE_READER_2.py` 和 `PLOT.py` 与 `RUN_and_PLOT.py` 在同一目录

---

## 许可证

本项目仅供学术与个人使用。
