import json

with open('c:/Users/Alejandro/GitHub/Layout_BESS_Tool/Layout.ipynb', 'r', encoding='utf-8') as f:
    nb = json.load(f)

code = []
for cell in nb['cells']:
    if cell['cell_type'] == 'code':
        source = "".join(cell['source'])
        source = source.replace("!pip install shapely matplotlib numpy", "")
        source = source.replace("✅", "")
        source = source.replace("plt.show()", "pass")
        code.append(source)

script = "\n\n".join(code)

custom = """
import time
import os

results = []
for mode in ["conservative", "aggressive", "ultra_aggressive", "hyper_pack"]:
    t0 = time.time()
    res = run_optimization(CONFIG, mode=mode, verbose=False)
    results.append(res)
    print(f"{mode:<20} -> {res['metrics']['bess_count']:>3} BESS in {time.time()-t0:>5.1f} s")

print_comparison(*results)

for res in results:
    fig, ax = plt.subplots(figsize=DEFAULT_STANDALONE_FIGSIZE)
    plot_layout(res["site"], res["non_buildable"], res["mvs_list"], res["bess_list"], CONFIG, ax=ax, title=_scenario_title(res))
    plt.savefig(f"c:/Users/Alejandro/.gemini/antigravity-ide/brain/b0c83c45-d2a7-43f9-9e6b-31b735c94fd3/{res['mode']}.png", bbox_inches='tight')
    plt.close(fig)

print("Images saved successfully.")
"""

script += custom

with open('c:/Users/Alejandro/GitHub/Layout_BESS_Tool/run_layout_test.py', 'w', encoding='utf-8') as f:
    f.write(script)
