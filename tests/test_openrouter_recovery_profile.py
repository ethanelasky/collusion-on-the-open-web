"""Budget boundaries for opt-in recovery profiles."""
import unittest

from ai_collusion.client import ModelConfig
from experiments.openrouter_recovery import recovery_profile


class RecoveryProfileTests(unittest.TestCase):
    def test_deepseek_starts_at_100k_and_qwen_keeps_its_budget(self):
        for name, initial, ceiling in [('deepseek-v4.1-flash', 100000, 100000),
                                       ('qwen3.8-27b', 16384, 32768)]:
            with self.subTest(name=name):
                model = ModelConfig(name=name, model=name, transport='openai',
                                    extra_body={'reasoning': {'enabled': True}})
                profile = recovery_profile(model)
                self.assertEqual(profile.max_tokens, initial)
                self.assertEqual(profile.tool_retry_max_tokens, ceiling)
                self.assertEqual(profile.extra_body['reasoning'], model.extra_body['reasoning'])
                self.assertEqual(model.max_tokens, 4096)
                self.assertNotIn('provider', model.extra_body)


if __name__ == '__main__':
    unittest.main()
