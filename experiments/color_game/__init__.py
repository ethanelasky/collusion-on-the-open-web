"""Notebook-first Schelling color game, separate from the historical experiment."""
from .config import COLORS, SETTINGS, GameConfig, arm_configs, make_plan
from .game import load_rollout, run_rollout
from .model import ModelAgent
from .prompts import build_system_prompt
from .batch import run_all_configs

__all__ = ["COLORS", "SETTINGS", "GameConfig", "ModelAgent", "arm_configs",
           "make_plan", "run_rollout", "run_all_configs", "load_rollout", "build_system_prompt"]
