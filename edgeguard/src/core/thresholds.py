from dataclasses import dataclass
import os


@dataclass
class Thresholds:
    n_pick_wrist_shelf_frames: int = 5
    m_pick_away_frames: int = 6
    s_putback_static_frames: int = 10
    k_conceal_missing_frames: int = 8
    c_counter_stable_frames: int = 12
    th_global_match: float = 0.72
    risk_green_max: float = 8.0
    risk_red_min: float = 12.0


def load_thresholds() -> Thresholds:
    return Thresholds(
        n_pick_wrist_shelf_frames=int(os.getenv("N_PICK_WRIST_SHELF_FRAMES", "5")),
        m_pick_away_frames=int(os.getenv("M_PICK_AWAY_FRAMES", "6")),
        s_putback_static_frames=int(os.getenv("S_PUTBACK_STATIC_FRAMES", "10")),
        k_conceal_missing_frames=int(os.getenv("K_CONCEAL_MISSING_FRAMES", "8")),
        c_counter_stable_frames=int(os.getenv("C_COUNTER_STABLE_FRAMES", "12")),
        th_global_match=float(os.getenv("TH_GLOBAL_MATCH", "0.72")),
        risk_green_max=float(os.getenv("RISK_GREEN_MAX", "8")),
        risk_red_min=float(os.getenv("RISK_RED_MIN", "12")),
    )
