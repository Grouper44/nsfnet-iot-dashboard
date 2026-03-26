"""
NSFNET IoT Dashboard — Level 1 模擬器
======================================
將訓練好的 PPO model 週期性執行，
把每輪 arc 狀態 + throughput 推送到 Google Apps Script (Level 2 雲端)

使用前設定：
  1. 將 APPS_SCRIPT_URL 改成你的 Apps Script 部署 URL
  2. 確認 ARCS_FILE / PATHS_FILE / MODEL_PATH 路徑正確
  3. python simulator.py
"""

import numpy as np
import pandas as pd
import json
import os
import time
import requests
import torch as th
import torch.nn as nn
import gymnasium as gym
from gymnasium import spaces
from stable_baselines3 import PPO
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from datetime import datetime

# ============================================================
# 使用者設定區
# ============================================================
APPS_SCRIPT_URL  = "https://script.google.com/macros/s/AKfycbydtf5HG75RUEUF7gkrQLZs4S9tmsi-uic_mqgEJjja5xYMnRfp8cYSOXkiVz1sZB8s/exec"
INTERVAL_SECONDS = 10       # 每幾秒推送一次
MAX_ROUNDS       = 0        # 0 = 無限執行，否則跑到指定輪數就停止

# 路徑設定（相對於本檔案，或改成絕對路徑）
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
ARCS_FILE  = os.path.join(BASE_DIR, "nsfnet_multistate_arcs.csv")
PATHS_FILE = os.path.join(BASE_DIR, "nsfnet_paths.csv")
MODEL_PATH = os.path.join(BASE_DIR, "ppo_gnn_mss_best.zip")

# ============================================================
# 1. NetworkDataManager（同 train/evaluate 腳本）
# ============================================================
class NetworkDataManager:
    def __init__(self, arcs_path, paths_path):
        self.arcs_df = pd.read_csv(arcs_path)
        self.paths_df = pd.read_csv(paths_path)
        self.unique_arcs = self.arcs_df["arc_name"].unique()
        self.num_arcs = len(self.unique_arcs)
        self.arc_id_to_idx = {str(n).strip(): i for i, n in enumerate(self.unique_arcs)}

        self.arc_props = {}
        self.arc_max_cap = {}
        self.max_cap = 0.0
        for _, row in self.arcs_df.iterrows():
            try:
                dist_info = json.loads(row["distribution"])
                caps  = np.array(dist_info["capacity_states"], dtype=np.float32)
                probs = np.array(dist_info["probabilities"],   dtype=np.float32)
            except Exception:
                caps  = np.array([225.0], dtype=np.float32)
                probs = np.array([1.0],   dtype=np.float32)
            name = str(row["arc_name"]).strip()
            self.arc_props[name]   = {"cap_states": caps, "probs": probs}
            self.arc_max_cap[name] = float(caps.max())
            self.max_cap = max(self.max_cap, float(caps.max()))

        self.paths = {}
        for idx, row in self.paths_df.iterrows():
            seq = str(row["arc_list"])
            self.paths[idx] = [x.strip() for x in (seq.split(";") if ";" in seq else seq.split(","))]
        self.num_paths = len(self.paths)

        self.path_to_arc_indices = []
        for i in range(self.num_paths):
            self.path_to_arc_indices.append(
                [self.arc_id_to_idx[arc] for arc in self.paths[i]]
            )

        self.arc_to_paths = {}
        for p_idx in range(self.num_paths):
            for a_idx in self.path_to_arc_indices[p_idx]:
                self.arc_to_paths.setdefault(a_idx, []).append(p_idx)
        self.shared_arcs = {a: ps for a, ps in self.arc_to_paths.items() if len(ps) > 1}


# ============================================================
# 2. GNN Feature Extractor（同 train_v3/evaluate_v4）
# ============================================================
class MSS_GNN_Extractor(BaseFeaturesExtractor):
    def __init__(self, observation_space: spaces.Box, dm: NetworkDataManager, features_dim: int = 128):
        super().__init__(observation_space, features_dim)
        self.dm = dm
        self.num_arcs  = dm.num_arcs
        self.num_paths = dm.num_paths
        self.path_indices = dm.path_to_arc_indices

        self.link_encoder = nn.Sequential(
            nn.Linear(1, 64), nn.ReLU(),
            nn.Linear(64, 64), nn.ReLU(),
        )
        self.path_post = nn.Sequential(nn.Linear(64, 64), nn.ReLU())
        self.bottleneck_encoder = nn.Sequential(
            nn.Linear(dm.num_paths, 64), nn.ReLU(),
            nn.Linear(64, 64), nn.ReLU(),
        )
        combined_dim = dm.num_paths * 64 + 64
        self.final_layer = nn.Sequential(
            nn.Linear(combined_dim, 256), nn.ReLU(),
            nn.Linear(256, features_dim), nn.ReLU(),
        )

    def forward(self, observations: th.Tensor) -> th.Tensor:
        batch_size = observations.shape[0]
        arc_obs     = observations[:, :self.num_arcs]
        path_bn_obs = observations[:, self.num_arcs:]
        links_in    = arc_obs.unsqueeze(-1)
        link_feats  = self.link_encoder(links_in)

        path_embeddings = []
        for p_idx in range(self.num_paths):
            relevant = link_feats[:, self.path_indices[p_idx], :]
            feat, _  = th.min(relevant, dim=1)
            path_embeddings.append(self.path_post(feat))

        gnn_flat = th.stack(path_embeddings, dim=1).view(batch_size, -1)
        bn_feat  = self.bottleneck_encoder(path_bn_obs)
        return self.final_layer(th.cat([gnn_flat, bn_feat], dim=1))


# ============================================================
# 3. 環境（用來取樣 + 取得 obs，同 evaluate_v4）
# ============================================================
class NSFNet_MSS_Env(gym.Env):
    def __init__(self, arcs_file, paths_file):
        super().__init__()
        self.dm = NetworkDataManager(arcs_file, paths_file)
        self.time_limit = 50
        self.arc_change_prob = 0.08
        self.action_space = spaces.Box(low=-5.0, high=5.0,
                                       shape=(self.dm.num_paths,), dtype=np.float32)
        obs_dim = self.dm.num_arcs + self.dm.num_paths
        self.observation_space = spaces.Box(low=0.0, high=1.0,
                                            shape=(obs_dim,), dtype=np.float32)
        self.current_arc_capacities = np.zeros(self.dm.num_arcs)
        self.max_cap = self.dm.max_cap if self.dm.max_cap > 0 else 225.0

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        rng  = np.random.default_rng(seed)
        caps = np.zeros(self.dm.num_arcs, dtype=np.float32)
        for i, arc_name in enumerate(self.dm.unique_arcs):
            props  = self.dm.arc_props[arc_name]
            caps[i] = rng.choice(props["cap_states"], p=props["probs"])
        self.current_arc_capacities = caps
        self.current_time = 0
        return self._get_obs(), {}

    def _get_obs(self):
        arc_obs  = np.clip(self.current_arc_capacities / self.max_cap, 0, 1).astype(np.float32)
        path_bns = np.array([self._compute_path_bottleneck(i) for i in range(self.dm.num_paths)])
        path_obs = np.clip(path_bns / self.max_cap, 0, 1).astype(np.float32)
        return np.concatenate([arc_obs, path_obs])

    def _compute_path_bottleneck(self, path_idx):
        bn = float("inf")
        for arc in self.dm.paths[path_idx]:
            idx = self.dm.arc_id_to_idx.get(arc)
            bn  = min(bn, self.current_arc_capacities[idx])
        return bn

    def step(self, action):
        self.current_time += 1
        terminated = self.current_time >= self.time_limit
        return self._get_obs(), 0.0, terminated, False, {}


# ============================================================
# 4. 吞吐量計算（含共享 arc 容量限制，同 evaluate_v4）
# ============================================================
def compute_throughput(ratios, bottlenecks, arc_capacities, dm):
    total_available = bottlenecks.sum()
    if total_available < 1.0:
        return 0.0, 0.0

    sending_rate   = total_available
    flow_requested = ratios * sending_rate
    path_pass      = np.ones(len(ratios))

    for arc_idx in range(dm.num_arcs):
        arc_cap    = arc_capacities[arc_idx]
        paths_using = dm.arc_to_paths.get(arc_idx, [])
        if len(paths_using) == 0:
            continue
        total_on_arc = sum(flow_requested[k] for k in paths_using)
        if total_on_arc > arc_cap and total_on_arc > 1e-6:
            scale = arc_cap / total_on_arc
            for k in paths_using:
                path_pass[k] = min(path_pass[k], scale)

    actual = (flow_requested * path_pass).sum()
    efficiency = actual / sending_rate if sending_rate > 0 else 0.0
    return actual, efficiency


# ============================================================
# 5. arc 狀態分類
# ============================================================
def classify_arc_status(cap, max_cap):
    if cap <= 0:
        return "failed"
    elif cap < max_cap * 0.5:
        return "degraded"
    else:
        return "normal"


# ============================================================
# 6. 推送到 Google Apps Script
# ============================================================
class _NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)):  return int(obj)
        if isinstance(obj, (np.floating,)): return float(obj)
        if isinstance(obj, np.ndarray):     return obj.tolist()
        return super().default(obj)

def push_to_apps_script(url, payload, timeout=10):
    try:
        resp = requests.post(url, data=json.dumps(payload, cls=_NumpyEncoder),
                             headers={"Content-Type": "application/json"}, timeout=timeout)
        if resp.status_code == 200:
            return True, resp.text
        else:
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except requests.exceptions.ConnectionError:
        return False, "Connection error"
    except requests.exceptions.Timeout:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)


# ============================================================
# 7. 主迴圈
# ============================================================
def main():
    print("=" * 60)
    print("  NSFNET IoT Dashboard — Level 1 模擬器")
    print("=" * 60)

    # 7.1 驗證必要檔案
    for path, label in [(ARCS_FILE, "arcs CSV"), (PATHS_FILE, "paths CSV"), (MODEL_PATH, "PPO model")]:
        if not os.path.exists(path):
            print(f"[ERROR] 找不到 {label}：{path}")
            print("  請將以下檔案複製到本資料夾（或修改頂端路徑設定）：")
            print("    nsfnet_multistate_arcs.csv")
            print("    nsfnet_paths.csv")
            print("    saved_model/ppo_gnn_mss_best.zip")
            return

    # 7.2 載入環境
    print(f"\n[INFO] 載入環境...")
    env = NSFNet_MSS_Env(ARCS_FILE, PATHS_FILE)
    dm  = env.dm
    print(f"  arcs: {dm.num_arcs}, paths: {dm.num_paths}, max_cap: {env.max_cap}")

    # 7.3 載入 PPO model
    print(f"[INFO] 載入模型：{MODEL_PATH}")
    custom_objects = {
        "policy_kwargs": dict(
            features_extractor_class=MSS_GNN_Extractor,
            features_extractor_kwargs=dict(dm=dm, features_dim=128),
            net_arch=dict(pi=[256, 128], vf=[256, 128]),
        )
    }
    model = PPO.load(MODEL_PATH, custom_objects=custom_objects, device="cpu")
    print("  模型載入成功")

    if APPS_SCRIPT_URL.startswith("https://script.google.com"):
        print(f"[INFO] Apps Script URL 已設定")
    else:
        print(f"[WARN] Apps Script URL 尚未設定，資料將只列印不推送")

    print(f"\n[INFO] 開始模擬，間隔 {INTERVAL_SECONDS} 秒\n")
    print("-" * 60)

    rng   = np.random.default_rng()  # 不固定 seed，每次隨機
    round_num = 0

    while True:
        round_num += 1
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 7.4 取樣 arc 容量
        caps = np.zeros(dm.num_arcs, dtype=np.float32)
        for i, arc_name in enumerate(dm.unique_arcs):
            props   = dm.arc_props[arc_name]
            caps[i] = rng.choice(props["cap_states"], p=props["probs"])

        env.current_arc_capacities = caps
        obs = env._get_obs()

        # 7.5 PPO 決策
        action, _ = model.predict(obs, deterministic=True)
        exp_a     = np.exp(action - np.max(action))
        ppo_ratios = exp_a / exp_a.sum()

        # 7.6 ECMP baseline
        ecmp_ratios = np.ones(dm.num_paths) / dm.num_paths

        # 7.7 計算瓶頸
        bottlenecks = np.array([env._compute_path_bottleneck(i) for i in range(dm.num_paths)])

        # 7.8 吞吐量
        ppo_tp,  ppo_eff  = compute_throughput(ppo_ratios,  bottlenecks, caps, dm)
        ecmp_tp, ecmp_eff = compute_throughput(ecmp_ratios, bottlenecks, caps, dm)

        # 7.9 arc 狀態
        arc_states = []
        for i, arc_name in enumerate(dm.unique_arcs):
            max_c = dm.arc_max_cap[arc_name]
            cap   = float(caps[i])
            # 估算 PPO 分配給經過此 arc 的流量
            paths_using = dm.arc_to_paths.get(i, [])
            flow_on_arc = sum(ppo_ratios[p] * bottlenecks.sum() for p in paths_using)
            util = min(flow_on_arc / cap, 1.0) if cap > 0 else 0.0

            arc_states.append({
                "arc_id":       arc_name,
                "capacity":     round(cap, 1),
                "max_capacity": round(max_c, 1),
                "utilization":  round(util, 3),
                "status":       classify_arc_status(cap, max_c),
            })

        # 7.10 統計
        failed   = sum(1 for a in arc_states if a["status"] == "failed")
        degraded = sum(1 for a in arc_states if a["status"] == "degraded")
        normal   = sum(1 for a in arc_states if a["status"] == "normal")

        # 7.11 組裝 payload
        payload = {
            "action":           "snapshot",
            "timestamp":        ts,
            "round":            round_num,
            "ppo_throughput":   round(float(ppo_tp),  2),
            "ecmp_throughput":  round(float(ecmp_tp), 2),
            "ppo_efficiency":   round(float(ppo_eff)  * 100, 2),
            "ecmp_efficiency":  round(float(ecmp_eff) * 100, 2),
            "total_available":  round(float(bottlenecks.sum()), 1),
            "active_arcs":      normal,
            "degraded_arcs":    degraded,
            "failed_arcs":      failed,
            "ppo_ratios":       [round(float(r), 4) for r in ppo_ratios],
            "arc_states":       arc_states,
        }

        # 7.12 列印
        improvement = ((ppo_tp - ecmp_tp) / ecmp_tp * 100) if ecmp_tp > 0 else 0
        print(f"[{ts}] Round {round_num:4d} | "
              f"PPO: {ppo_tp:6.1f} Mbps ({ppo_eff*100:.1f}%) | "
              f"ECMP: {ecmp_tp:6.1f} Mbps ({ecmp_eff*100:.1f}%) | "
              f"+{improvement:.1f}% | "
              f"normal={normal} degraded={degraded} failed={failed}")

        # 7.13 推送
        if "script.google.com" in APPS_SCRIPT_URL:
            ok, msg = push_to_apps_script(APPS_SCRIPT_URL, payload)
            if not ok:
                print(f"  [WARN] 推送失敗：{msg}")

        # 7.14 結束判斷
        if MAX_ROUNDS > 0 and round_num >= MAX_ROUNDS:
            print(f"\n[INFO] 已完成 {MAX_ROUNDS} 輪，模擬結束。")
            break

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
