import json
from engine import run_bess_optimization
from visualization import print_comparison

with open('Layout.ipynb', 'r') as f:
    nb = json.load(f)

# Extract config from the notebook
config_code = "".join(nb['cells'][1]['source'])
exec(config_code) # defines CONFIG

res = run_bess_optimization(CONFIG, mode="conservative", verbose=False)
print("Modularization test successful. BESS placed:", res['metrics']['bess_count'])
