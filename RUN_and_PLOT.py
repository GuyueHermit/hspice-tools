"""
HSPICE 一键仿真 + 解析 + 绘图 — 给定 .sp 文件，自动完成全流程

═══════════════════════════════════════════════════════════════════════════════
  使用方法:
      python run_and_plot.py                    ← 直接运行，用下面的 CONFIG
      python run_and_plot.py <sp文件路径>       ← 命令行覆盖配置
═══════════════════════════════════════════════════════════════════════════════

  执行流程:
      1. 调用 hspice 仿真 .sp 文件
      2. 解析 .lis → 保存 CSV / NPZ / JSON 到  data/
      3. 画图 → 保存 PNG 到  figures/

  前置条件:
      - HSPICE 已安装且在 PATH 中
      - HSPICE_READER_2.py 和 plot_lis.py 与本文件同目录
"""

import sys
import subprocess
import shutil
from pathlib import Path

# ── 将本脚本所在目录加入 sys.path，以便 import 同目录的工具模块 ──
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import HSPICE_READER_2 as reader
import PLOT as plotter


# ============================================================================
#  ╔══════════════════════════════════════════════════════════════════════╗
#  ║                     ► 在这里修改参数 ◄                              ║
#  ║  改完直接 python run_and_plot.py 运行即可                           ║
#  ╚══════════════════════════════════════════════════════════════════════╝
# ============================================================================

# ── HSPICE .sp 文件路径 ────────────────────────────────────────────────────
SP_FILE = r'c:\Users\kotsu\Desktop\CODE\HSPICE_TEST\Mosfet_Bulk\dev\hspice\AvalancheCircuit2_BulkMOSFET.sp'
# 例: SP_FILE = r"C:\Users\kotsu\Desktop\SPICE\dev\hspice\AvalancheCircuit2_BulkMOSFET.sp"
# 例: SP_FILE = "./simulation.sp"

# ── 横坐标 (X 轴) ─────────────────────────────────────────────────────────
X_VAR = "time"
# 例: X_VAR = "V(d)"

# ── 纵坐标 (Y 轴) ─────────────────────────────────────────────────────────
Y_VARS = ['Vd (v)', 'I_neg (A)']

# 例: Y_VARS = ["V1 (V)", "R (Ω)"]   ← 双纵轴

# ── 自定义计算列（与 HSPICE_READER_2.py 中的 CUSTOM_CALC 格式相同）
#   取消注释即可启用，注释掉则跳过。
CUSTOM_CALC = [
    # (列名, 操作, [变量名...], [符号...])
    # ("R (Ω)",   "div", ["V1 (V)",  "Iv1 (A)"], [+1, -1]),
    # ("P (W)",   "mul", ["V1 (V)",  "Iv1 (A)"], [+1, +1]),
    ("I_neg (A)", "neg", ["Iv_d (A)"],           [+1]),
]

# ── 图表标题 ──────────────────────────────────────────────────────────────
TITLE = None
# 例: TITLE = "Id vs Vd Hysteresis"

# ── HSPICE 可执行文件 ─────────────────────────────────────────────────────
#   None = 自动搜索 PATH ("hspice")
HSPICE_EXE = None
# 例: HSPICE_EXE = r"C:\synopsys\Hspice_P-2019.06-SP1-1\WIN64\hspice.exe"

# ── 输出子目录 ───────────────────────────────────────────────────────────
#   相对于本脚本所在目录
DATA_DIR     = "data"         # CSV / NPZ / JSON 保存到这里
FIGURES_DIR  = "figures"      # PNG 保存到这里

# ── 是否弹出窗口显示图片 ─────────────────────────────────────────────────
#   False = 只保存文件
#   True  = 保存文件 + 弹出 matplotlib 窗口
SHOW_PLOT = False

# ── 仿真超时 (秒) ─────────────────────────────────────────────────────────
TIMEOUT = 300

# ============================================================================
#  核心流程 — 通常不需要修改
# ============================================================================

def find_hspice() -> str:
    """找到 hspice 可执行文件。"""
    if HSPICE_EXE:
        path = Path(HSPICE_EXE)
        if path.exists():
            return str(path)
        # 也许只是可执行文件名
        if shutil.which(HSPICE_EXE):
            return HSPICE_EXE

    # 自动搜索
    for name in ['hspice', 'hspice.exe', 'hspice.com']:
        found = shutil.which(name)
        if found:
            return found

    raise FileNotFoundError(
        "找不到 hspice。请设置 HSPICE_EXE 或将其加入系统 PATH。\n"
        "  例: HSPICE_EXE = r'C:\\synopsys\\Hspice_P-2019.06-SP1-1\\WIN64\\hspice.exe'"
    )


def run_hspice(sp_path: Path, lis_path: Path) -> bool:
    """运行 HSPICE 仿真，返回是否成功。"""
    hspice = find_hspice()
    lis_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [hspice, '-i', str(sp_path), '-o', str(lis_path)]
    print(f"[hspice] {' '.join(cmd)}")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=str(sp_path.parent),  # 在 .sp 所在目录运行，确保 .include 路径正确
        )
    except subprocess.TimeoutExpired:
        print(f"[hspice] 超时 ({TIMEOUT} s)，仿真可能不收敛。")
        return False

    if proc.returncode != 0:
        print(f"[hspice] 返回码 {proc.returncode}")
        # 打印最后几行错误
        err_lines = proc.stderr.strip().split('\n')
        for line in err_lines[-10:]:
            print(f"  {line}")
        return False

    if not lis_path.exists():
        print(f"[hspice] lis 文件未生成: {lis_path}")
        return False

    print(f"[hspice] 仿真完成 → {lis_path}  ({lis_path.stat().st_size / 1024:.0f} KB)")
    return True


def run_all(sp_path: str | Path,
            x_var: str = "V(d)",
            y_vars=None,
            title: str = None,
            show: bool = False):
    """一站式：仿真 → 解析 → 画图。

    Args:
        sp_path:  .sp 文件路径
        x_var:    横坐标变量名
        y_vars:   纵坐标变量名（字符串或列表）
        title:    图表标题
        show:     是否弹窗显示
    """
    sp_path = Path(sp_path).resolve()
    if not sp_path.exists():
        raise FileNotFoundError(f"SP 文件不存在: {sp_path}")

    stem = sp_path.stem

    # ── 输出目录 ──
    data_dir    = HERE / DATA_DIR
    figures_dir = HERE / FIGURES_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. HSPICE 仿真 ──
    lis_path = sp_path.parent / f'{stem}.lis'
    print("=" * 60)
    print("Step 1/3: HSPICE 仿真")
    print("=" * 60)
    if not run_hspice(sp_path, lis_path):
        raise RuntimeError("HSPICE 仿真失败")

    # ── 2. 解析 lis → CSV / NPZ ──
    print("\n" + "=" * 60)
    print("Step 2/3: 解析 .lis → CSV / NPZ")
    print("=" * 60)
    result = reader.parse_lis(str(lis_path))

    # 计算自定义列
    custom_names, custom_data = reader.compute_custom_columns(
        result['data'], result['var_names'], CUSTOM_CALC
    )
    if custom_names:
        result['custom_names'] = custom_names
        result['custom_data']  = custom_data
        print(f"\n✦ 自定义列: {', '.join(custom_names)}")
    else:
        result['custom_names'] = []
        result['custom_data']  = None

    reader.print_summary(result)
    saved = reader.save_all(result, lis_path, out_dir=data_dir)

    # ── 3. 画图 ──
    print("\n" + "=" * 60)
    print("Step 3/3: 画图")
    print("=" * 60)

    # 加载刚保存的 npz
    data = plotter.load_data(str(saved['npz']))

    x_idx, y_indices = plotter._resolve_variables(
        data, x_var, y_vars, interactive=False
    )

    # 确定输出图片路径
    # 如果是单条曲线，用变量名命名；多条曲线用 stem
    if isinstance(y_vars, str) and ',' not in y_vars:
        fig_name = f'{stem}_{_safe_name(x_var)}_vs_{_safe_name(y_vars)}.png'
    elif isinstance(y_vars, (list, tuple)) and len(y_vars) == 1:
        fig_name = f'{stem}_{_safe_name(x_var)}_vs_{_safe_name(y_vars[0])}.png'
    else:
        fig_name = f'{stem}_plot.png'

    fig_path = figures_dir / fig_name

    plotter.plot(data, x_idx, y_indices, title=title,
                 output=str(fig_path))

    if show:
        import matplotlib.pyplot as plt
        plt.show()

    print()
    print("── 输出文件 ──")
    for key, path in saved.items():
        print(f"  {key}: {path}")
    print(f"  png: {fig_path}")

    return {'data': saved, 'figure': fig_path}


def _safe_name(s: str) -> str:
    """把变量名转成安全的文件名片段。V(d) → Vd, I(v_d) → Ivd"""
    return s.replace('(', '').replace(')', '').replace('_', '').replace(' ', '')


# ============================================================================
#  入口
# ============================================================================

def main():
    import argparse

    p = argparse.ArgumentParser(
        description='HSPICE 一键仿真 + 解析 + 绘图'
    )
    p.add_argument('sp_file', nargs='?', default=None,
                   help='.sp 文件路径 (不传则使用 CONFIG 中的 SP_FILE)')
    p.add_argument('-x', '--xvar', default=None)
    p.add_argument('-y', '--yvar', default=None)
    p.add_argument('-t', '--title', default=None)
    p.add_argument('--show', action='store_true', default=None,
                   help='弹出窗口显示图片')
    args = p.parse_args()

    # ── 合并: 命令行 > CONFIG ──
    sp_file = args.sp_file or SP_FILE
    x_var   = args.xvar   or X_VAR
    y_vars  = args.yvar   or Y_VARS
    title   = args.title  or TITLE
    show    = args.show if args.show is not None else SHOW_PLOT

    if not sp_file:
        print("错误: 未指定 .sp 文件。请设置 CONFIG 区中的 SP_FILE 或用命令行传参。")
        print("用法:")
        print("  1. 编辑 run_and_plot.py 顶部 CONFIG 区，设置 SP_FILE = 'xxx.sp'")
        print("  2. 或: python run_and_plot.py <sp文件路径>")
        sys.exit(1)

    try:
        run_all(sp_file, x_var=x_var, y_vars=y_vars, title=title, show=show)
    except Exception as e:
        print(f"\n失败: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()



