# 主张—证据—图组的轻量契约

工具只做可复算的结构与一致性检查，不解析原始谱图，不裁定机制真伪，不输出科学认证分数。助手按当前已有信息填入，用户不必制作JSON。

## 字段

- `conditions[]`：`id`，以及已知的`sample`、`soc`（含定义/充放方向）、`cycle`、`temperature_c`、`voltage_window`、`mass_basis`；未知用null。
- `evidence[]`：`id`、`method`、`origin_id`（原始数据族）、`condition_id`、`inspection`（text/caption/image/raw/model）、可选`source_doi`。FT/WT共享XAS的origin_id，DRT与拟合共享EIS的origin_id。
- `claims[]`：`id`、`text`、`kind`（observation/inference/hypothesis/causal）、`evidence_ids[]`；可选`quantity`、`asserted_independent_sources`、`controls[]`、`alternatives[]`。工具不推断重复次数或替代机制。
- 依赖文献证据的主张加`requires_literature_support: true`，在关联evidence里提供`source_doi`，在claim的`support_scope`说明具体支持范围。未找到直接支持时可用`basis: "general_theory"`及明确`support_note`，输出仍须标“当前未找到直接文献支持”。工具只查这些字段是否存在，不认证DOI真实性或支持匹配。
- `figures[]`：`id`、`question`、`claim_ids[]`和`panels[]`。面板含`id`、`evidence_ids[]`、`condition_id`，可含`color_role`和`hex`。面板ID在整个任务内唯一。
- `comparisons[]`：`id`、`panel_ids[]`、`purpose`（same_state/performance）、`intentional_differences[]`。只有有意且已解释的变量差异才填此列表，不能用它掩盖分母不相容。
- `palette`：`source_type`（original_proposal/historical_visual_approximation）、`exact_author_colors`（false）、`roles`（角色到HEX）、`kind`；热图可有`colorbar`对象（vmin/vmax/unit/center）。

`assets/task-example.json` 为虚构的通用教学输入，不是用户研究，不是实际实验结论。

## 审计输出

- `errors`：重复ID、悬空引用、缺身份、隔离来源参与论证、结构格式错误。
- `review_items`：需人工判断的状态/分母不匹配、同源被多算、未核图面、因果缺少对照说明、颜色角色冲突等。
- `scientific_acceptance` 永远为 `false`：无错误只代表这些有限检查未发现问题。

对于表面价态、谱学拟合、DOS/NEB/DFT等科学内容，工具只给常见越界提示，实际判断仍由原文、测量条件、对照和专业分析完成。自动工具不能保证发现所有错误，不能用审核通过字样代替解释。

CLI退出码：0=无结构错误（可能仍有review_items）；2=输入或结构错误。检索0条是真实无匹配，不自动返回不相关论文。
