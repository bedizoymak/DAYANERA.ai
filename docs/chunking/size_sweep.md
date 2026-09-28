# Chunk size experiment

| metric | tiny | current | medium | large | no_section_leaf |
|---|---|---|---|---|---|
| chunks_retrievable | 4308 | 3615 | 3054 | 2534 | 3796 |
| chunks_parent | 587 | 430 | 333 | 275 | 534 |
| tokens_median | 103.0 | 138 | 128.0 | 89.5 | 123.0 |
| tokens_p95 | 291 | 306 | 324 | 426 | 303 |
| tokens_max | 490 | 525 | 525 | 737 | 525 |
| tiny_chunks_lt25 | 0.1193 | 0.1408 | 0.165 | 0.1973 | 0.1467 |
| formula_complete_unit | 15/15 | 15/15 | 15/15 | 15/15 | 15/15 |
| passages_in_context_avg | 3.94 | 3.92 | 3.87 | 3.85 | 3.98 |
| all_hit@1 | 0.9231 | 0.9231 | 0.9231 | 0.9038 | 0.9038 |
| all_mrr | 0.9455 | 0.9455 | 0.9455 | 0.9359 | 0.9391 |
| all_answer_grounding | 0.9808 | 0.9808 | 0.9808 | 0.9808 | 0.9808 |
| all_formula_context_complete | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| heldout_hit@1 | 0.75 | 0.75 | 0.75 | 0.6667 | 0.6667 |
| heldout_mrr | 0.8194 | 0.8194 | 0.8194 | 0.7778 | 0.7917 |
| heldout_answer_grounding | 0.9167 | 0.9167 | 0.9167 | 0.9167 | 0.9167 |

Configurations: tiny = {'soft_target': 110, 'hard_max': 240, 'leaf_section_max': 190, 'parent_max': 480, 'table_leaf_max': 190, 'table_group_target': 130}; current = ChunkingConfig defaults; medium = {'soft_target': 250, 'hard_max': 520, 'leaf_section_max': 440, 'parent_max': 1030, 'table_leaf_max': 440, 'table_group_target': 300}; large = {'soft_target': 390, 'hard_max': 750, 'leaf_section_max': 640, 'parent_max': 1500, 'table_leaf_max': 640, 'table_group_target': 440}; no_section_leaf = {'leaf_section_max': 0}
