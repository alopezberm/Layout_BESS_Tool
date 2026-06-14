import json
from engine import run_bess_optimization
from visualization import print_comparison

with open('Layout.ipynb', 'r') as f:
    nb = json.load(f)

config_code = "".join(nb['cells'][1]['source'])
exec(config_code) # defines CONFIG

print("Running Conservative Mode...")
res_cons = run_bess_optimization(CONFIG, mode="conservative", verbose=True)

print("Running Hyper-Pack Mode...")
res_hyp = run_bess_optimization(CONFIG, mode="hyper_pack", verbose=True)
