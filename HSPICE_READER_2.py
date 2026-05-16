"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  HSPICE .lis 解析器 — 提取瞬态仿真数据并导出为 CSV / NPZ / JSON          ║
║                                                                          ║
║  功能：                                                                  ║
║    1. 从 HSPICE 输出的 .lis 文件中定位 "****** transient analysis" 段   ║
║    2. 提取所有 x...y 数据块中的电压 / 电流波形                          ║
║    3. 解析 .MEASURE 语句的测量结果                                        ║
║    4. 提取直流工作点 (OP) 信息                                            ║
║    5. 输出为三种格式：CSV（通用表格）、NPZ（Python快速加载）、JSON（元数据）║
║                                                                          ║
║  使用方式：                                                              ║
║      python HSPICE_READER_2.py                ← 使用 CONFIG 中的配置     ║
║      python HSPICE_READER_2.py <lis路径>      ← 命令行覆盖 lis 文件路径  ║
║      python HSPICE_READER_2.py --no-save      ← 仅打印摘要，不保存文件   ║
║      python HSPICE_READER_2.py -o ./results   ← 指定输出目录              ║
║                                                                          ║
║  输出文件：                                                              ║
║      <basename>_data.csv    — CSV 表格（可导入 Origin / Excel / pyplot） ║
║      <basename>_data.npz    — NumPy 压缩存档（Python 快速加载）           ║
║      <basename>_info.json   — .MEASURE 结果 + 直流工作点 + 变量元信息    ║
║                                                                          ║
║  Python 加载示例：                                                        ║
║      >>> import numpy as np                                              ║
║      >>> d = np.load('xxx_data.npz')                                    ║
║      >>> d['time']        # (N,)      时间轴（秒）                       ║
║      >>> d['data']        # (N, M)    数据矩阵，每列为一个变量            ║
║      >>> d['var_names']   # (M,)      变量名列表                          ║
║      >>> d['var_types']   # (M,)      类型：'voltage' 或 'current'       ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import re
import sys
import json
import argparse
import numpy as np
from pathlib import Path


# ============================================================================
#  ╔══════════════════════════════════════════════════════════════════════╗
#  ║                    🎛️  用户配置区                                   ║
#  ║  修改下方参数后直接运行：python HSPICE_READER_2.py                   ║
#  ╚══════════════════════════════════════════════════════════════════════╝
# ============================================================================

# ── HSPICE .lis 文件路径 ──────────────────────────────────────────────────
#   类型：str 或 None
#   • 设为路径字符串 → 直接使用该文件
#   • 设为 None → 必须通过命令行参数传入，或自动搜索当前目录下的 .lis 文件
LIS_FILE = None   # 设为 None 后可通过命令行传入，或自动搜索当前目录下的 .lis 文件

# ── 输出目录 ──────────────────────────────────────────────────────────────
#   类型：str 或 None
#   • 字符串 → 使用指定目录（相对路径相对于脚本所在目录，绝对路径直接使用）
#   • None → 输出到脚本所在目录下的 data/ 子文件夹
OUTPUT_DIR = 'data' 

# ── 仅打印模式 ────────────────────────────────────────────────────────────
#   类型：bool
#   • False → 正常解析并保存 CSV / NPZ / JSON 文件
#   • True  → 仅打印解析摘要到终端，不写任何文件（用于快速查看内容）
NO_SAVE = False


# ── 自定义计算列 ──────────────────────────────────────────────────────────
#   在 CSV / NPZ 的末尾追加用户自定义的派生列。
#   每行定义一列，格式为：
#     (列名, 操作类型, [变量1, 变量2, ...], [符号1, 符号2, ...])
#
#   操作类型说明:
#     "div"  → 变量1 ÷ 变量2（二元，顺序敏感）
#     "mul"  → 变量1 × 变量2（二元）
#     "add"  → 变量1 + 变量2（二元）
#     "sub"  → 变量1 − 变量2（二元，顺序敏感）
#     "neg"  → −变量1（一元，取反）
#
#   符号说明：每个变量乘以对应符号（+1 或 −1）后再参与运算。
#   例如电流方向反转用 −1。
#
#   变量名必须与 .lis 文件中的 var_names 完全一致，如 "V1" "Iv1"。
#   取消注释即可启用，注释掉则跳过。
#
#   示例（请根据实际仿真的变量名修改）：
#     ("R (Ω)",   "div", ["V1 (V)",  "Iv1 (A)"], [+1, -1]),
#     ("P (W)",   "mul", ["V1 (V)",  "Iv1 (A)"], [+1, +1]),
#     ("I_neg (A)", "neg", ["Iv1"],           [+1]),

CUSTOM_CALC = [
    # ====== 在这里添加你的自定义列 ======
    # (列名, 操作, [变量名...], [符号...])
    #
    # 取消下面三行的注释来启用示例：
    ("R (Ω)",   "div", ["V1 (V)",  "Iv1 (A)"], [+1, -1]),
    ("P (W)",   "mul", ["V1 (V)",  "Iv1 (A)"], [+1, +1]),
    ("I_neg (A)", "neg", ["Iv1"],           [+1]),
]


# ============================================================================
#  常量定义
# ============================================================================

# HSPICE 单位后缀 → 数值乘数映射表
# HSPICE 使用后缀表示数量级（如 "1u" = 1×10⁻⁶, "1k" = 1×10³）
# 参考：https://zh.wikipedia.org/wiki/国际单位制词头
SUFFIX_MAP: dict[str, float] = {
    'f': 1e-15,          # 飞（飞秒/飞法）
    'p': 1e-12,          # 皮（皮秒/皮法）
    'n': 1e-9,           # 纳（纳秒）
    'u': 1e-6,           # 微（微秒/微安）
    'm': 1e-3,           # 毫（毫秒/毫伏）
    'k': 1e3,            # 千（千赫兹/千欧）
    'meg': 1e6,          # 兆（兆赫兹）—— HSPICE 用 meg 而非 M
    'x': 1e6,            # 兆（x 是 meg 的别名，某些老版本 HSPICE 使用）
    'g': 1e9,            # 吉（吉赫兹）
    't': 1e12,           # 太
    'a': 1e-18,          # 阿（atto）
}

# 脚本所在目录（用于默认输出路径）
# 注意：使用 resolve() 而非 absolute()，因为 resolve() 会解析符号链接
SCRIPT_DIR: Path = Path(__file__).resolve().parent


# ============================================================================
#  数值解析
# ============================================================================

def parse_hspice_number(s: str) -> float:
    """
    将 HSPICE 格式的数值字符串转换为 Python float。

    HSPICE 用后缀表示数量级，例如：
        "1.5u"  → 1.5 × 10⁻⁶ = 0.0000015
        "10n"   → 10 × 10⁻⁹  = 0.00000001
        "2.5k"  → 2.5 × 10³  = 2500.0

    也兼容标准科学计数法，如 "1.5e-6" → 0.0000015

    Args:
        s: HSPICE 格式的数值字符串（如 "1.5u", "10n", "2.5k", "1e-3"）

    Returns:
        解析后的浮点数值

    Raises:
        ValueError: 如果字符串格式无法解析

    Examples:
        >>> parse_hspice_number("1.5u")
        1.5e-06
        >>> parse_hspice_number("10n")
        1e-08
        >>> parse_hspice_number("1e-3")
        0.001
    """
    s = s.strip()
    if not s:
        # 空字符串 → 返回 0，避免后续计算崩溃
        return 0.0

    # 匹配模式：可选符号 + 数字（支持小数点 + 科学计数法）+ 可选单位后缀
    # 分组1: 数字部分（含符号、小数点、科学计数法）
    # 分组2: 单位后缀字母（如 u, n, p, k 等）
    m = re.match(r'^([-+]?\d+\.?\d*(?:[eE][-+]?\d+)?)\s*([a-zA-Z]*)$', s)
    if not m:
        raise ValueError(
            f"Cannot parse HSPICE number: '{s}' — "
            f"expected format like '1.5u', '10n', '2.5k', or '1e-3'"
        )

    value_str, suffix = m.group(1), m.group(2).lower()
    value = float(value_str)

    # 将 HSPICE 后缀转换为标准数值乘数
    if suffix in SUFFIX_MAP:
        value *= SUFFIX_MAP[suffix]
    # 若后缀不在映射表中，忽略它（某些 HSPICE 版本可能有无后缀的纯数字输出）

    return value


# ============================================================================
#  数据块提取
# ============================================================================

def _extract_blocks(text: str) -> list[dict]:
    """
    从 HSPICE 瞬态分析输出文本中提取所有 "x...y" 数据块。

    HSPICE 的瞬态分析输出格式如下：
        x
        time    v(1)    v(2)    ← 表头（变量类型：voltage, current 等）
        0.0     5.0     0.0     ← 数据行
        1n      4.8     0.2
        ...
        y

    其中 x 标记数据块开始，y 标记结束。
    一个 .lis 文件中可能包含多个 x...y 块（如 .PRINT 和 .PLOT 各生成一个）。

    Args:
        text: 整个 .lis 文件文本内容

    Returns:
        数据块列表，每个块包含：
        - 'headers': 变量类型列表（如 ['time', 'voltage', 'voltage']）
        - 'names':   变量名列表（如 ['', '1', '2']）
        - 'rows':    数据行列表（每行为字符串列表）

    设计说明：
    此函数并未尝试一次性匹配全部 x...y 块，
    而是使用循环逐块查找，这样即使中间有格式异常也能继续解析后续块。
    这是"防御性解析"的典型做法——宁可少解析一个块，也不让整个解析崩溃。
    """
    # 定位瞬态分析段的起始位置
    # 注意：.lis 文件中可能还有其他仿真类型（DC、AC 等），
    # 我们只关心 TRANSIENT 部分
    tran_idx = text.find('****** transient analysis')
    if tran_idx == -1:
        raise ValueError("No 'transient analysis' section found in lis file")

    tail = text[tran_idx:]  # 只处理瞬态分析段，忽略之前的内容
    blocks = []
    pos = 0

    # 循环查找每个数据块，直到找不到下一个 x...y 为止
    while True:
        # 查找块开始标记 "x"（位于行首）
        x_match = re.search(r'^x\s*$', tail[pos:], re.MULTILINE)
        if not x_match:
            break  # 没有更多数据块了
        block_start = pos + x_match.start()

        # 查找块结束标记 "y"（位于行首）
        y_match = re.search(r'^y\s*$', tail[block_start:], re.MULTILINE)
        if not y_match:
            break  # 有 x 无 y → 数据块不完整，跳过
        block_end = block_start + y_match.start()

        # 提取 x 和 y 之间的全部文本
        block_text = tail[block_start:block_end]
        lines = block_text.strip().split('\n')

        # --- 解析表头 ---
        header_line = ''
        subheader_line = ''
        data_start = 0

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == 'x' or stripped == '':
                continue  # 跳过标记行和空行
            # 找包含 "time" 的行作为表头（HSPICE 的标准格式）
            # ❗ 注意：如果 .lis 文件中变量名不包含 "time"，此逻辑会失效
            # 此时所有行都会被跳过，块解析为空
            if 'time' in stripped.lower() and not header_line:
                header_line = stripped
                if i + 1 < len(lines):
                    # HSPICE 有时会在变量类型行（header）下方
                    # 紧跟一行变量名（subheader），如：
                    #   voltage   voltage   current    ← header
                    #   1         2         3          ← subheader（节点名）
                    subheader_line = lines[i + 1].strip()
                    data_start = i + 2
                break

        # --- 提取数据行 ---
        data_lines = []
        for i in range(data_start, len(lines)):
            stripped = lines[i].strip()
            if stripped == '' or stripped == 'y':
                continue  # 跳过空行和结束标记
            data_lines.append(stripped)

        if header_line and data_lines:
            blocks.append({
                'headers': header_line.split(),
                'names': subheader_line.split() if subheader_line else [],
                'rows': [line.split() for line in data_lines],
            })

        # 移动到下一个块
        pos = block_end + 1

    return blocks


# ============================================================================
#  主解析函数
# ============================================================================

def parse_lis(filepath: str) -> dict:
    """
    解析 HSPICE .lis 文件，返回包含所有仿真数据的结构化字典。

    解析流程：
      1. 读取文件 → 2. 定位瞬态分析段 → 3. 提取 x...y 数据块
      → 4. 解析数值 → 5. 合并多个块的数据 → 6. 提取 .MEASURE 结果
      → 7. 提取直流工作点

    关于多个数据块的合并策略：
      某些 HSPICE 版本可能将不同变量输出到不同的 x...y 块中。
      本函数会以第一个块的时间轴为基准，对其他块的时间轴进行插值对齐。
      这意味着即使不同块的时间点不一致，最终也能得到统一的时间轴。

    Args:
        filepath: .lis 文件的路径

    Returns:
        包含以下键的字典：
        - 'time':         ndarray, 形状 (N,), 时间轴（秒）
        - 'data':         ndarray, 形状 (N, M), 数据矩阵（M 个变量）
        - 'var_names':    list[str], 长度 M, 变量全名（如 "V1", "I(R1)"）
        - 'var_types':    list[str], 长度 M, 变量类型（"voltage" / "current"）
        - 'measurements': dict,  .MEASURE 语句的测量结果
        - 'op_point':     dict,  直流工作点参数

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 文件中未找到瞬态分析段或数据块
        UnicodeDecodeError: 文件编码不是 UTF-8（尝试以 replace 模式处理）
    """
    # 读取文件内容（使用 errors='replace' 容错非 UTF-8 字符）
    # ❗ 注意：如果文件很大（>100MB），一次性读入会占用较多内存
    # 对于 HSPICE 仿真输出，通常文件大小在几 MB 到几十 MB 之间，可以接受
    text = Path(filepath).read_text(encoding='utf-8', errors='replace')

    # 定位瞬态分析段
    tran_idx = text.find('****** transient analysis')
    if tran_idx == -1:
        raise ValueError("No 'transient analysis' section found in lis file. "
                         "Make sure the simulation contains a .TRAN statement.")

    # 提取数据块
    blocks = _extract_blocks(text)
    if not blocks:
        raise ValueError("No data blocks (x...y) found in lis file. "
                         "Make sure the simulation contains a .PRINT TRAN statement.")

    # --- 解析每个数据块 ---
    all_times = []          # 每个块的时间轴
    all_data_blocks = []    # 每个块的每列数据
    all_var_names = []      # 每个块的变量全名
    all_var_types = []      # 每个块的变量类型

    for blk in blocks:
        raw_rows = blk['rows']
        if not raw_rows:
            continue

        n_cols = len(raw_rows[0])  # 第一列是时间，其余是变量数据
        if n_cols < 2:
            continue  # 只有时间列没有数据 → 跳过

        time_col = []
        data_cols = [[] for _ in range(n_cols - 1)]

        # 逐行解析数值
        for row in raw_rows:
            if len(row) < 2:
                continue  # ❗ 保护性检查：防止 HSPICE 输出空行
            time_col.append(parse_hspice_number(row[0]))
            for j, val_str in enumerate(row[1:]):
                if j < len(data_cols):
                    data_cols[j].append(parse_hspice_number(val_str))

        if not time_col:
            continue

        # --- 构建变量名 ---
        headers = blk['headers'][1:]  # 第一个是 "time"，跳过
        names = blk['names'] if blk['names'] else []
        n_vars = len(data_cols)

        for j in range(n_vars):
            vtype = headers[j].lower() if j < len(headers) else 'unknown'
            vname = names[j] if j < len(names) else f'var{j}'

            # 生成带单位的变量名，如 V1 (V), Iv1 (A), R1 (Ω)
            if vtype == 'voltage':
                full_name = f'V{vname} (V)'
            elif vtype == 'current':
                full_name = f'I{vname} (A)'
            else:
                full_name = f'{vtype}_{vname}'

            all_var_names.append(full_name)
            all_var_types.append(vtype)
            all_data_blocks.append(data_cols[j])

        all_times.append(time_col)

    # --- 合并数据：以第一个块的时间轴为基准 ---
    # ❗ 重要假设：第一个数据块的时间轴代表完整的仿真时间范围
    # 如果后续块的时间范围不同，会通过插值强制对齐
    ref_time = np.array(all_times[0])
    n_pts, n_vars = len(ref_time), len(all_data_blocks)
    data_matrix = np.zeros((n_pts, n_vars))

    # 收集每个变量的时间轴（用于后续判断是否需要插值）
    time_by_var = []
    for blk_idx, blk in enumerate(blocks):
        raw_rows = blk['rows']
        if not raw_rows:
            continue
        n_cols = len(raw_rows[0]) - 1
        for _ in range(n_cols):
            time_by_var.append(all_times[blk_idx])

    # 将每个变量的数据填充到统一矩阵中
    for j in range(n_vars):
        d = np.array(all_data_blocks[j])
        t = np.array(time_by_var[j])

        if len(t) == n_pts and np.allclose(t, ref_time):
            # 时间轴完全一致 → 直接赋值（最常见情况，零开销）
            data_matrix[:, j] = d
        else:
            # 时间轴不同 → 线性插值
            # ❗ 注意：np.interp 不会外推，如果目标时间超出源时间范围，
            #   会使用边界值填充。这可能导致数据在边缘处失真。
            data_matrix[:, j] = np.interp(ref_time, t, d)

    # --- 解析 .MEASURE 语句结果 ---
    # HSPICE 的 .MEASURE 输出格式：
    #   trigger_time = 1.234e-09  at=  1.234e-09   ← 单点测量
    #   trise_time = 5.678e-10  from=  1.000e-09  to=  6.000e-09  ← 区间测量
    measurements = {}

    # 匹配 "xxx = value at= time" 格式（单点测量）
    for name, value, at_time in re.findall(
        r'^\s*(\w+)\s*=\s*(\S+)\s+at=\s*(\S+)', text[tran_idx:], re.MULTILINE
    ):
        try:
            measurements[name] = {
                'value': parse_hspice_number(value),
                'at_time': parse_hspice_number(at_time),
            }
        except (ValueError, IndexError):
            pass  # 忽略解析失败的测量项

    # 匹配 "xxx = value from= t1 to= t2" 格式（区间测量，如 Trig、Rise 等）
    for name, value, t0, t1 in re.findall(
        r'^\s*(\w+)\s*=\s*(\S+)\s+from=\s*(\S+)\s+to=\s*(\S+)',
        text[tran_idx:], re.MULTILINE
    ):
        if name not in measurements:  # 避免覆盖同名的单点测量
            try:
                measurements[name] = {
                    'value': parse_hspice_number(value),
                    'from': parse_hspice_number(t0),
                    'to': parse_hspice_number(t1),
                }
            except (ValueError, IndexError):
                pass

    # --- 解析直流工作点 (Operating Point) ---
    # HSPICE 输出格式：
    #   ****** operating point
    #   v(1) = 5.0000   v(2) = 0.0000   i(vdd) = -1.000e-03
    op_point = {}
    op_match = re.search(
        r'\*\*\*\*\*\* operating point.*?\n(.*?)(?=\n\s*\*)',
        text, re.DOTALL
    )
    if op_match:
        for line in op_match.group(1).split('\n'):
            # 匹配 "variable = value" 对
            for m in re.finditer(r'(\S+)\s*=\s*(\S+)', line):
                name, val = m.group(1), m.group(2)
                try:
                    op_point[name] = parse_hspice_number(val)
                except ValueError:
                    op_point[name] = val  # 解析失败则保留原始字符串

    return {
        'time': ref_time,
        'data': data_matrix,
        'var_names': all_var_names,
        'var_types': all_var_types,
        'measurements': measurements,
        'op_point': op_point,
    }


# ============================================================================
#  文件保存
# ============================================================================

def save_csv(result: dict, filepath: Path) -> None:
    """
    将解析结果保存为 CSV 格式。

    CSV 格式兼容性：
      - Origin:  File → Import → CSV（自动识别列名）
      - Excel:   直接双击打开
      - Python:  pandas.read_csv() / np.loadtxt()

    CSV 文件格式：
      第一行为表头：time, V(1), V(2), I(R1), ..., 自定义列1, 自定义列2
      之后每行为一个时间点的数据，科学计数法（%.8e 精度）。

    如果 result 中包含自定义列（'custom_names' 和 'custom_data'），
    会自动追加到 CSV 的最后一列或最后几列。

    Args:
        result:   parse_lis() 返回的字典（可含 custom_names / custom_data）
        filepath: 输出 CSV 文件路径
    """
    # 基础列名（时间 + 原始变量）
    all_headers = ['time'] + list(result['var_names'])

    # 基础数据
    cols_to_stack = [result['time'], result['data']]

    # 追加自定义列（如果有）
    custom_names = result.get('custom_names', [])
    custom_data = result.get('custom_data', None)
    if custom_names and custom_data is not None:
        all_headers.extend(custom_names)
        cols_to_stack.append(custom_data)

    # 组合为一个大矩阵
    stacked = np.column_stack(cols_to_stack)

    header = ','.join(all_headers)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(
        str(filepath), stacked, delimiter=',', header=header,
        comments='',  # !!! 关键：默认 comments='#' 会在表头前加 #，
                      # 导致 Origin 和 Excel 无法识别表头
        fmt='%.8e'    # 8 位有效数字的科学计数法，兼顾精度和可读性
    )
    print(f"[save] CSV  → {filepath}  "
          f"({stacked.shape[0]} rows × {stacked.shape[1]} cols"
          + (f", {len(custom_names)} custom" if custom_names else "")
          + ")")


def save_npz(result: dict, filepath: Path) -> None:
    """
    将解析结果保存为 NumPy .npz 压缩存档。

    NPZ 格式优势：
      - 加载速度最快（无需重复解析）
      - 支持无损压缩（文件小）
      - 保留数据类型（不丢失精度）
      - 适合作为 Python 脚本之间的数据交换格式

    如果 result 中包含自定义列，会额外保存以下键：
      - 'custom_names': 自定义列名
      - 'custom_data':  自定义列数据矩阵

    Args:
        result:   parse_lis() 返回的字典（可含 custom_names / custom_data）
        filepath: 输出 .npz 文件路径

    加载方法：
        d = np.load('xxx.npz')
        d['time'], d['data'], d['var_names'], d['var_types']
        # 自定义列：
        print(d['custom_names'])  # 如果存在
        print(d['custom_data'])   # 如果存在
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # 基础保存内容
    save_dict = {
        'time': result['time'],
        'data': result['data'],
        'var_names': np.array(result['var_names'], dtype=str),
        'var_types': np.array(result['var_types'], dtype=str),
    }

    # 追加自定义列（如果有）
    custom_names = result.get('custom_names', [])
    custom_data = result.get('custom_data', None)
    if custom_names and custom_data is not None:
        save_dict['custom_names'] = np.array(custom_names, dtype=str)
        save_dict['custom_data'] = custom_data

    np.savez_compressed(str(filepath), **save_dict)
    sz_kb = filepath.stat().st_size / 1024
    n_vars = result['data'].shape[1]
    n_custom = len(custom_names) if custom_names else 0
    print(f"[save] NPZ  → {filepath}  "
          f"({result['data'].shape[0]} × {n_vars}"
          + (f" + {n_custom} custom" if n_custom else "")
          + f", {sz_kb:.1f} KB)")


def save_info_json(result: dict, filepath: Path) -> None:
    """
    将 .MEASURE 结果、直流工作点和变量元信息保存为 JSON。

    此文件不包含波形数据（数据量大的话 JSON 效率低），
    而是专门用于记录仿真参数和测量结果，方便快速查阅。

    包含的信息：
      - 仿真时间范围和步长
      - 变量列表（名称、类型、索引）
      - .MEASURE 语句全部结果
      - 直流工作点全部参数

    Args:
        result:   parse_lis() 返回的字典
        filepath: 输出 .json 文件路径
    """

    def _convert(obj):
        """
        递归地将 NumPy 类型转换为 Python 原生类型。
        因为 json.dump 无法序列化 numpy.int64/numpy.float32 等类型。
        """
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, dict):
            return {k: _convert(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_convert(v) for v in obj]
        return obj

    # 自定义列信息（如果有）
    custom_names = result.get('custom_names', [])
    custom_data = result.get('custom_data', None)

    info = {
        'source_file': str(filepath),
        'num_timepoints': int(len(result['time'])),
        'time_start': float(result['time'][0]),
        'time_stop': float(result['time'][-1]),
        'time_unit': 'second',
        'num_variables': int(len(result['var_names'])),
        'variables': [
            {'name': n, 'type': t, 'index': i}
            for i, (n, t) in enumerate(zip(result['var_names'], result['var_types']))
        ],
        'custom_columns': [
            {'name': n, 'index': i}
            for i, n in enumerate(custom_names)
        ] if custom_names else [],
        'measurements': _convert(result['measurements']),
        'op_point': _convert(result['op_point']),
    }

    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(
        json.dumps(info, indent=2, ensure_ascii=False),
        encoding='utf-8'
    )
    print(f"[save] JSON → {filepath}")


def save_all(result: dict, lis_path: str | Path,
             out_dir: str | Path = None) -> dict:
    """
    一键保存全部三种格式（CSV + NPZ + JSON）到指定目录。

    Args:
        result:   parse_lis() 返回的字典
        lis_path: .lis 文件路径（仅用于提取文件名前缀作为输出文件名）
        out_dir:  输出目录
                  • None → 使用脚本目录下的 data/
                  • 相对路径 → 相对于脚本目录
                  • 绝对路径 → 直接使用

    Returns:
        包含输出文件路径的字典：
        {'csv': Path, 'npz': Path, 'json': Path}
    """
    lis_path = Path(lis_path)

    # 确定输出目录
    if out_dir is None:
        out_dir = SCRIPT_DIR / "data"
    else:
        out_dir = Path(out_dir)
        if not out_dir.is_absolute():
            out_dir = SCRIPT_DIR / out_dir

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = lis_path.stem  # 不带扩展名的文件名

    paths = {
        'csv':  out_dir / f'{stem}.csv',
        'npz':  out_dir / f'{stem}.npz',
        'json': out_dir / f'{stem}.json',
    }

    save_csv(result, paths['csv'])
    save_npz(result, paths['npz'])
    save_info_json(result, paths['json'])

    return paths


# ============================================================================
#  自定义计算列
# ============================================================================

def compute_custom_columns(data_matrix: np.ndarray, var_names: list[str],
                            custom_config: list) -> tuple[list[str], np.ndarray | None]:
    """
    根据 CUSTOM_CALC 配置，从已有数据列计算派生列。

    支持的运算:
      - "div":  col_a / col_b（逐元素除法）
      - "mul":  col_a * col_b（逐元素乘法）
      - "add":  col_a + col_b（逐元素加法）
      - "sub":  col_a - col_b（逐元素减法）
      - "neg":  -col_a（逐元素取反）

    每个变量在参与运算前会乘以对应的符号（+1 / -1）。
    如果变量名在 var_names 中找不到，会打印警告并跳过该列。

    Args:
        data_matrix:  形状 (N, M) 的数据矩阵
        var_names:    变量名列表，长度 M
        custom_config: 自定义配置列表，每个元素为
                       (列名, 操作, [变量名...], [符号...])

    Returns:
        (custom_names, custom_data):
          - custom_names: 自定义列名列表
          - custom_data:  形状 (N, K) 的矩阵，K = 自定义列数
                          如果没有自定义列，返回 None
    """
    if not custom_config:
        return [], None

    custom_names: list[str] = []
    custom_cols: list[np.ndarray] = []

    for entry in custom_config:
        # 解包配置
        col_name, operation, vars_needed, signs = entry

        # 查找每个变量在矩阵中的列索引
        col_indices = []
        missing = False
        for vname in vars_needed:
            try:
                col_indices.append(var_names.index(vname))
            except ValueError:
                print(f"  [警告] 自定义列 '{col_name}' 的变量 '{vname}' 在 .lis 中找不到")
                print(f"          现有变量: {var_names}")
                missing = True
                break

        if missing:
            continue  # 跳过这个自定义列

        # 读取各列数据并应用符号
        cols_signed = [
            data_matrix[:, idx] * sign
            for idx, sign in zip(col_indices, signs)
        ]

        # 执行运算
        if operation == "div":
            # 除法：col_a / col_b
            if len(cols_signed) != 2:
                print(f"  [警告] 'div' 需要 2 个变量，'{col_name}' 跳过")
                continue
            # ❗ 注意：被除数可能为零，少量除零会产生 inf 或 nan
            with np.errstate(divide='ignore', invalid='ignore'):
                result = cols_signed[0] / cols_signed[1]

        elif operation == "mul":
            if len(cols_signed) != 2:
                print(f"  [警告] 'mul' 需要 2 个变量，'{col_name}' 跳过")
                continue
            result = cols_signed[0] * cols_signed[1]

        elif operation == "add":
            if len(cols_signed) != 2:
                print(f"  [警告] 'add' 需要 2 个变量，'{col_name}' 跳过")
                continue
            result = cols_signed[0] + cols_signed[1]

        elif operation == "sub":
            if len(cols_signed) != 2:
                print(f"  [警告] 'sub' 需要 2 个变量，'{col_name}' 跳过")
                continue
            result = cols_signed[0] - cols_signed[1]

        elif operation == "neg":
            if len(cols_signed) != 1:
                print(f"  [警告] 'neg' 需要 1 个变量，'{col_name}' 跳过")
                continue
            result = -cols_signed[0]

        else:
            print(f"  [警告] 未知操作 '{operation}'，跳过 '{col_name}'")
            print(f"          支持: div, mul, add, sub, neg")
            continue

        custom_names.append(col_name)
        custom_cols.append(result)

    if not custom_cols:
        return [], None

    # 将所有自定义列堆叠为 (N, K) 矩阵
    custom_data = np.column_stack(custom_cols)
    return custom_names, custom_data


# ============================================================================
#  摘要输出
# ============================================================================

def print_summary(result: dict) -> None:
    """
    在终端打印解析结果的摘要信息。

    打印内容：
      - 时间点数量和范围
      - 变量列表（含类型）
      - 数据矩阵的维度
      - .MEASURE 结果（如有）
      - 直流工作点（如有，最多显示前 10 项）

    Args:
        result: parse_lis() 返回的字典
    """
    print("=" * 60)
    print("HSPICE .lis 解析结果")
    print("=" * 60)

    print(f"\n时间点数量:  {len(result['time'])}")
    print(f"时间范围:    {result['time'][0]:.6e} ~ {result['time'][-1]:.6e} s")
    print(f"变量数量:    {len(result['var_names'])}")

    print(f"\n变量列表:")
    for i, (name, vtype) in enumerate(
        zip(result['var_names'], result['var_types'])
    ):
        print(f"  [{i:2d}] {name:20s}  ({vtype})")

    # 打印自定义列（如果有）
    custom_names = result.get('custom_names', [])
    custom_data = result.get('custom_data', None)
    if custom_names and custom_data is not None:
        print(f"\n自定义列:")
        for i, name in enumerate(custom_names):
            print(f"  [{i:2d}★] {name:20s}  (derived)")

    n_base = result['data'].shape[1]
    n_custom = len(custom_names) if custom_names else 0
    n_total = n_base + n_custom
    print(f"\n数据矩阵: {n_base} 原始列"
          + (f" + {n_custom} 自定义列 = 共 {n_total} 列" if n_custom else ""))
    print(f"  时间点: {result['data'].shape[0]}")

    if result['measurements']:
        print(f"\n.MEASURE 结果:")
        for name, info in result['measurements'].items():
            print(f"  {name}: {info}")

    if result['op_point']:
        print(f"\n直流工作点 (前{min(10, len(result['op_point']))}项):")
        for i, (k, v) in enumerate(result['op_point'].items()):
            if i >= 10:
                print(f"  ... ({len(result['op_point']) - 10} more)")
                break
            print(f"  {k} = {v}")


# ============================================================================
#  入口函数
# ============================================================================

def main():
    """
    脚本入口：解析命令行参数 → 定位 .lis 文件 → 解析 → 保存。

    命令行参数优先级高于 CONFIG 区配置：
        命令行指定 → CONFIG 配置 → 自动搜索当前目录 .lis

    流程：
        1. 解析 argparse 参数
        2. 确定 .lis 文件路径
        3. 调用 parse_lis() 解析
        4. 调用 print_summary() 打印摘要
        5. 调用 save_all() 保存文件（除非 --no-save）
    """
    parser = argparse.ArgumentParser(
        description='解析任意 HSPICE .lis 文件的瞬态仿真数据 → CSV + NPZ + JSON',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  %(prog)s                                         使用 CONFIG 配置
  %(prog)s simulation.lis                          指定 .lis 文件
  %(prog)s simulation.lis -o ./results             指定输出目录
  %(prog)s simulation.lis --no-save                仅打印，不保存
        """,
    )
    parser.add_argument(
        'lis_file', nargs='?', default=None,
        help='HSPICE .lis 文件路径（不传则使用 CONFIG 中的 LIS_FILE）',
    )
    parser.add_argument(
        '-o', '--outdir', default=None,
        help='输出目录（默认: CONFIG 中的 OUTPUT_DIR，若未设置则为脚本所在目录的 data/）',
    )
    parser.add_argument(
        '--no-save', action='store_true', default=None,
        help='仅打印摘要，不保存文件',
    )
    args = parser.parse_args()

    # ── 合并配置：命令行参数 > CONFIG 常量 > 默认值 ──
    # 这样设计的好处：既支持"一键运行"（用 CONFIG），
    # 也支持"灵活调用"（用命令行参数），满足不同使用场景
    lis_file  = args.lis_file or LIS_FILE
    out_dir   = args.outdir   or OUTPUT_DIR
    no_save   = args.no_save if args.no_save is not None else NO_SAVE

    # ---- 确定 .lis 文件路径 ----
    if lis_file:
        # 使用 CONFIG 或命令行指定的路径
        lis_path = Path(lis_file).resolve()
    else:
        # 未指定任何路径 → 自动搜索当前目录下的 .lis 文件
        cwd = Path.cwd()
        candidates = list(cwd.glob('*.lis'))

        if len(candidates) == 0:
            print(f"错误: 未指定 .lis 文件路径，且当前目录 ({cwd}) 没有 .lis 文件。")
            print(f"解决方法:")
            print(f"  1. 编辑本文件顶部的 LIS_FILE 变量，设置路径")
            print(f"  2. 或: python {Path(__file__).name} <lis文件路径>")
            sys.exit(1)
        elif len(candidates) == 1:
            lis_path = candidates[0]
            print(f"自动选择: {lis_path}")
        else:
            # 多个 .lis 文件 → 提示用户明确指定
            print(f"当前目录有多个 .lis 文件，请通过命令行参数明确指定:")
            for c in candidates:
                print(f"  python {Path(__file__).name} {c.name}")
            sys.exit(1)

    # 检查文件是否存在
    if not lis_path.exists():
        print(f"错误: 文件不存在: {lis_path}")
        sys.exit(1)

    # ---- 解析 .lis 文件 ----
    print(f"读取: {lis_path}")
    try:
        result = parse_lis(str(lis_path))
    except PermissionError as e:
        print(f"权限错误: 无法读取 {lis_path}")
        print(f"  {e}")
        print(f"  提示: 检查文件是否被其他程序锁定（如 HSPICE 仍在运行）")
        sys.exit(1)
    except Exception as e:
        print(f"解析失败: {e}")
        print(f"  提示: .lis 文件格式可能与预期不符")
        print(f"  请确认文件中包含 '****** transient analysis' 段和 x...y 数据块")
        sys.exit(1)

    # ---- 计算自定义列（如配置） ----
    custom_names, custom_data = compute_custom_columns(
        result['data'], result['var_names'], CUSTOM_CALC
    )
    if custom_names:
        result['custom_names'] = custom_names
        result['custom_data']  = custom_data
        print(f"\n✦ 自定义列: {', '.join(custom_names)}")
    else:
        result['custom_names'] = []
        result['custom_data']  = None

    # 打印摘要
    print_summary(result)

    # ---- 保存文件 ----
    if no_save:
        print("\n(未保存文件，使用了 --no-save 模式)")
        return

    if out_dir is None:
        out_dir = SCRIPT_DIR / "data"
    else:
        out_dir = Path(out_dir)
        if not out_dir.is_absolute():
            out_dir = SCRIPT_DIR / out_dir
    print(f"\n输出目录: {out_dir}")

    try:
        saved = save_all(result, lis_path, out_dir)
    except PermissionError as e:
        print(f"权限错误: 无法写入 {out_dir}")
        print(f"  改用桌面目录试试:")
        print(f"  python {Path(__file__).name} {lis_path} \\")
        print(f"    -o C:\\Users\\{Path.home().name}\\Desktop")
        print(f"  详细: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"保存失败: {e}")
        sys.exit(1)

    # ---- 打印加载提示 ----
    print()
    print("── 加载示例 ──")
    print(f"  # Python / pyplot:")
    print(f"  d = np.load(r'{saved['npz']}')")
    print(f"  x, y = d['time'], d['data']")
    print(f"  plt.plot(x, y[:, 0], label=str(d['var_names'][0]))")
    print(f"")
    print(f"  # Origin:  File → Import → CSV → {saved['csv'].name}")
    print(f"  # Excel:   双击 {saved['csv'].name}")


if __name__ == '__main__':
    main()






