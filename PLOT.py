"""
HSPICE 数据通用绘图工具 — 从 HSPICE_READER_2 输出的 CSV / NPZ 中选取任意列画 X‑Y 曲线

═══════════════════════════════════════════════════════════════════════════════
  使用方法:
      python plot.py                              ← 直接运行，用下面的 CONFIG
      python plot.py -x time -y "V1,R (Ω)"     ← 命令行覆盖配置
      python plot.py -l                           ← 仅列出数据文件中所有变量
═══════════════════════════════════════════════════════════════════════════════

  支持的文件格式:  .npz / .csv（均由 HSPICE_READER_2.py 生成）
  变量名支持模糊匹配: 写 "v1" 可匹配 "V1"
  双纵轴: 第 1 条曲线在左轴，第 2 条及之后自动分配右轴
"""

import sys
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path


# ============================================================================
#  ╔══════════════════════════════════════════════════════════════════════╗
#  ║                     ► 在这里修改参数 ◄                              ║
#  ╚══════════════════════════════════════════════════════════════════════╝
# ============================================================================

# ── 数据文件路径 ──────────────────────────────────────────────────────────
DATA_FILE = None

# ── 横坐标 (X 轴) ────────────────────────────────────────────────────────
X_VAR = "time"

# ── 纵坐标 (Y 轴) ────────────────────────────────────────────────────────
Y_VARS = "V1"

# ── 输出图片路径 ─────────────────────────────────────────────────────────
OUTPUT = None

# ── 图表标题 ─────────────────────────────────────────────────────────────
TITLE = None

# ── 图片尺寸 / 分辨率 ────────────────────────────────────────────────────
FIG_WIDTH  = 9
FIG_HEIGHT = 6
DPI        = 150

# ── 线条样式 ─────────────────────────────────────────────────────────────
LINE_WIDTH  = 1.5   # 普通线宽
LINE_WIDTH2 = 2.0   # 电阻/重点曲线线宽
SHOW_GRID   = True
GRID_ALPHA  = 0.3

# ── 轴标签字体大小 ───────────────────────────────────────────────────────
LABEL_FONTSIZE = 13
TITLE_FONTSIZE = 14

# ── 交互模式 ─────────────────────────────────────────────────────────────
INTERACTIVE = False


# ============================================================================
#  以下为函数实现，通常不需要修改
# ============================================================================

SCRIPT_DIR: Path = Path(__file__).resolve().parent

# ── 单位后缀映射 ──────────────────────────────────────────────────────────
UNITS = {
    'voltage': 'V', 'current': 'A', 'time': 's',
    'charge': 'C', 'power': 'W', 'resistance': chr(937),
    'capacitance': 'F', 'inductance': 'H',
}


# ── 辅助: 去掉变量名中的单位后缀 ────────────────────────────────────────
def _strip_unit(name: str) -> str:
    """'V1 (V)' → 'V1', 'time (s)' → 'time'"""
    import re
    m = re.match(r'^(.+?)\s*\(.*?\)\s*$', name)
    return m.group(1).strip() if m else name.strip()


# ── 数据加载 ──────────────────────────────────────────────────────────────

def _load_npz(filepath: str) -> dict:
    d = np.load(filepath, allow_pickle=True)
    t = d['time']
    mat = d['data']
    data = np.column_stack([t, mat])
    var_names = ['time'] + [str(n) for n in d['var_names']]
    var_types = ['time'] + [str(t) for t in d['var_types']]
    custom_names = d.get('custom_names', None)
    custom_data  = d.get('custom_data',  None)
    if custom_names is not None and custom_data is not None:
        for i, nm in enumerate(custom_names):
            var_names.append(str(nm))
            var_types.append('custom')
        if custom_data.ndim == 1:
            custom_data = custom_data.reshape(-1, 1)
        data = np.column_stack([data, custom_data])
    return {'time': t, 'data': data, 'var_names': var_names, 'var_types': var_types}


def _load_csv(filepath: str) -> dict:
    mat = np.loadtxt(filepath, delimiter=',', skiprows=1)
    with open(filepath, 'r', encoding='utf-8') as f:
        header_line = f.readline().strip()
    headers = header_line.split(',')
    var_names = ['time'] + headers[1:]
    var_types = ['time']
    for name in headers[1:]:
        n = name.strip()
        if n.startswith('V') or n.startswith('v'):
            var_types.append('voltage')
        elif n.startswith('I') or n.startswith('i'):
            var_types.append('current')
        elif chr(937) in n or 'ohm' in n.lower():
            var_types.append('resistance')
        elif 'W' in n or 'power' in n.lower():
            var_types.append('power')
        else:
            var_types.append('custom')
    return {'time': mat[:, 0], 'data': mat, 'var_names': var_names, 'var_types': var_types}


def load_data(filepath: str) -> dict:
    ext = Path(filepath).suffix.lower()
    if ext == '.npz':
        return _load_npz(filepath)
    elif ext == '.csv':
        return _load_csv(filepath)
    raise ValueError(f"不支持的文件格式: {ext}")


# ── 列查找 ────────────────────────────────────────────────────────────────

def find_column(var_names: list[str], query: str) -> int:
    query = query.strip().strip('"').strip("'")
    if query.lower() == 't':
        query = 'time'
    if query in var_names:
        return var_names.index(query)
    for i, name in enumerate(var_names):
        if name.lower() == query.lower():
            return i
    candidates = [(i, name) for i, name in enumerate(var_names)
                  if query.lower() in name.lower()]
    if len(candidates) == 1:
        print(f"  [模糊匹配] '{query}' => [{candidates[0][0]}] {candidates[0][1]}")
        return candidates[0][0]
    if len(candidates) > 1:
        print(f"  '{query}' 匹配到多个变量:")
        for idx, name in candidates:
            print(f"    [{idx}] {name}")
        raise ValueError("请用完整名称指定")
    raise ValueError(f"找不到变量: '{query}'")


def list_variables(var_names: list[str], var_types: list[str]) -> None:
    print("\n可用变量:")
    for i, (name, vtype) in enumerate(zip(var_names, var_types)):
        print(f"  [{i:2d}] {name:22s} ({vtype})")


def _resolve_variables(data: dict, x_spec, y_spec, interactive: bool):
    var_names = data['var_names']
    if x_spec is None:
        if interactive:
            list_variables(var_names, data['var_types'])
            print("\n横坐标 (X):")
            try:
                idx = int(input("  输入索引: ").strip())
                if 0 <= idx < len(var_names):
                    x_spec = var_names[idx]
            except (ValueError, EOFError):
                x_spec = input("  输入变量名: ").strip()
        else:
            raise ValueError("未指定横坐标。请设置 X_VAR 或用 -x 传参。")
    x_idx = find_column(var_names, x_spec)
    y_specs = y_spec
    if y_specs is None:
        if interactive:
            list_variables(var_names, data['var_types'])
            print("\n纵坐标 (Y，逗号分隔多条曲线):")
            y_input = input("  输入: ").strip()
            y_specs = [s.strip() for s in y_input.split(',') if s.strip()]
        else:
            raise ValueError("未指定纵坐标。请设置 Y_VARS 或用 -y 传参。")
    elif isinstance(y_specs, str):
        y_specs = [s.strip() for s in y_specs.split(',') if s.strip()]
    elif isinstance(y_specs, (list, tuple)):
        y_specs = list(y_specs)
    y_indices = [find_column(var_names, spec) for spec in y_specs]
    return x_idx, y_indices


# ── 核心绘图 ──────────────────────────────────────────────────────────────

def plot(data: dict, x_idx: int, y_indices: list[int],
         title: str = None, output: str = None,
         figsize: tuple = None, dpi: int = None,
         linewidth: float = None, grid: bool = None,
         label_fontsize: int = None, title_fontsize: int = None):
    """
    核心绘图函数。
    - 第 1 条曲线画在左轴
    - 第 2 条及之后曲线分配右轴 (twinx)，完全独立刻度
    """
    if figsize is None:
        figsize = (FIG_WIDTH, FIG_HEIGHT)
    if dpi is None:
        dpi = DPI
    if linewidth is None:
        linewidth = LINE_WIDTH
    if grid is None:
        grid = SHOW_GRID
    if label_fontsize is None:
        label_fontsize = LABEL_FONTSIZE
    if title_fontsize is None:
        title_fontsize = TITLE_FONTSIZE

    x = data['data'][:, x_idx]
    x_name = data['var_names'][x_idx]
    x_type = data['var_types'][x_idx]
    x_unit = UNITS.get(x_type, '')
    x_name_clean = _strip_unit(x_name)  # 去掉变量名中已自带的单位括号

    fig, ax1 = plt.subplots(figsize=figsize)
    ax2 = None

    # 高区分度配色
    PALETTE = ['#E63946', '#457B9D', '#2A9D8F', '#E76F51',
               '#8338EC', '#FFBE0B', '#FB5607', '#3A86FF']

    # ── 左轴：第 1 条曲线 ──
    y0  = data['data'][:, y_indices[0]]
    n0  = data['var_names'][y_indices[0]]  # 列名已含单位，如 "V1 (V)"
    c0  = PALETTE[0]
    lw0 = LINE_WIDTH2 if data['var_types'][y_indices[0]] == 'resistance' else linewidth

    ax1.plot(x, y0, linewidth=lw0, color=c0, label=n0)
    ax1.set_ylabel(n0, fontsize=label_fontsize, color=c0)
    ax1.tick_params(axis='y', labelcolor=c0)

    # ── 右轴：第 2 条及之后的曲线（若有）──
    for j in range(1, len(y_indices)):
        yj     = data['data'][:, y_indices[j]]
        nj     = data['var_names'][y_indices[j]]  # 列名已含单位
        cj     = PALETTE[j % len(PALETTE)]
        lwj    = LINE_WIDTH2 if data['var_types'][y_indices[j]] == 'resistance' else linewidth

        if ax2 is None:
            ax2 = ax1.twinx()
            ax2.spines['right'].set_visible(True)

        ax2.plot(x, yj, linewidth=lwj, color=cj, label=nj)
        ax2.set_ylabel(nj, fontsize=label_fontsize, color=cj)
        ax2.tick_params(axis='y', labelcolor=cj)

    # ── X 轴标签（用干净的变量名 + 标准单位） ──
    ax1.set_xlabel(f'{x_name_clean} ({x_unit})' if x_unit else x_name_clean,
                   fontsize=label_fontsize)

    # ── 图例（合并左右轴） ──
    h1, l1 = ax1.get_legend_handles_labels()
    if ax2 is not None:
        h2, l2 = ax2.get_legend_handles_labels()
        ax1.legend(h1 + h2, l1 + l2, fontsize=10, loc='best')
    else:
        ax1.legend(fontsize=10, loc='best')

    # ── 标题 ──
    ystr = ', '.join(data['var_names'][i] for i in y_indices)
    ax1.set_title(title or f'{ystr} vs {x_name}', fontsize=title_fontsize)

    # ── 网格 ──
    if grid:
        ax1.grid(True, alpha=GRID_ALPHA, linestyle='--')
    if 'time' in x_name.lower():
        ax1.ticklabel_format(axis='x', style='sci', scilimits=(-3, 3))

    fig.tight_layout()

    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output, dpi=dpi, bbox_inches='tight')
        print(f"\n图片已保存: {output}")
    else:
        plt.show()


# ============================================================================
#  入口
# ============================================================================

def main():
    import argparse
    p = argparse.ArgumentParser(description='HSPICE 数据通用绘图工具')
    p.add_argument('data_file', nargs='?', default=None)
    p.add_argument('-x', '--xvar', default=None)
    p.add_argument('-y', '--yvar', default=None)
    p.add_argument('-o', '--output', default=None)
    p.add_argument('-t', '--title', default=None)
    p.add_argument('-l', '--list', action='store_true', help='仅列出变量')
    p.add_argument('-W', '--width', type=float, default=None, help='图片宽度（英寸），覆盖 FIG_WIDTH')
    p.add_argument('-H', '--height', type=float, default=None, help='图片高度（英寸），覆盖 FIG_HEIGHT')
    args = p.parse_args()

    data_file = args.data_file or DATA_FILE
    x_var     = args.xvar     or X_VAR
    y_var     = args.yvar     or Y_VARS
    output    = args.output   or OUTPUT
    title     = args.title    or TITLE
    fig_w     = args.width    or FIG_WIDTH
    fig_h     = args.height   or FIG_HEIGHT

    if not data_file:
        print("错误: 未指定数据文件。请设置 DATA_FILE 或用命令行传参。")
        sys.exit(1)
    data_file = Path(data_file)
    if not data_file.exists():
        print(f"错误: 文件不存在: {data_file}")
        sys.exit(1)

    print(f"读取: {data_file}")
    data = load_data(str(data_file))
    print(f"数据: {data['data'].shape[0]} 点 x {data['data'].shape[1]} 变量")

    if args.list:
        list_variables(data['var_names'], data['var_types'])
        return

    try:
        x_idx, y_indices = _resolve_variables(data, x_var, y_var, INTERACTIVE)
    except ValueError as e:
        print(f"变量错误: {e}")
        list_variables(data['var_names'], data['var_types'])
        sys.exit(1)

    # 自动输出路径 —— 【电路文件名】_【x轴】_【y轴】.png
    if output is None:
        fig_dir = SCRIPT_DIR / "figures"
        fig_dir.mkdir(parents=True, exist_ok=True)
        x_name = _strip_unit(data['var_names'][x_idx])
        y_names = '_'.join(_strip_unit(data['var_names'][i]) for i in y_indices)
        out_path = fig_dir / f"{data_file.stem}_{x_name}_{y_names}.png"
    elif output.lower() == 'show':
        out_path = None
    else:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)

    plot(data, x_idx, y_indices, title=title,
         output=str(out_path) if out_path else None,
         figsize=(fig_w, fig_h))


if __name__ == '__main__':
    main()

