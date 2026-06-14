<skills>
  <summary>Available local skills that the agent can inspect with read_file.</summary>
  <skill name="ideate" path="skills/ideate/SKILL.md">
    <description>Wiki 增强研究构思 — 基于 wiki 知识库的研究想法生成。扫描论文局限性和开放问题，通过结构化路径生成研究想法，过滤后保存为 wiki idea 页面。支持反重复机制（banlist）。</description>
  </skill>
  <skill name="paper-ingest" path="skills/paper-ingest/SKILL.md">
    <description>论文导入 — 将学术论文导入 Wiki 知识库，自动创建论文页、概念页、方法页、数据集页、作者页，条件触发 Survey 和 Comparison 页面。</description>
  </skill>
  <skill name="paper-update" path="skills/paper-update/SKILL.md">
    <description>论文增量更新 — 更新已导入的论文版本，同步更新关联的概念页、方法页和作者页。</description>
  </skill>
  <skill name="paper-wiki" path="skills/paper-wiki/SKILL.md">
    <description>学术论文 Wiki 知识库 — 架构概览、页面类型、引用规范、故障排查。具体流程见 paper-ingest、paper-update、wiki-lint、wiki-ask 四个子 Skill。</description>
  </skill>
  <skill name="quick-lookup" path="skills/quick-lookup/SKILL.md">
    <description>快速联网查询工具，基于 Tavily Search API。支持天气查询、事实检索、新闻动态、官方文档查找、实时行情等场景。用户要求搜索、联网、查官网、核实事实、获取最新信息时使用。</description>
  </skill>
  <skill name="rag-skill" path="skills/rag-skill/SKILL.md">
    <description>面向本地知识库目录的检索和问答助手。核心流程：(1)分层索引导航 (2)遇到PDF/Excel时必须先读取references学习处理方法 (3)处理文件后再检索。按文件类型组合使用 grep、Read、pdfplumber、pandas 进行渐进式检索，避免整文件加载。用户问题涉及"从知识库目录回答问题/检索信息/查资料"时使用。</description>
  </skill>
  <skill name="retry-lesson-capture" path="skills/retry-lesson-capture/SKILL.md">
    <description>当任务首次失败但重试其他方案成功时，将可复用经验沉淀到 workspace/MEMORY.md 和当前 SKILL.md 中。适用于 API 失败切换备用、命令失败改用其他命令、解析失败改用其他流程等场景。</description>
  </skill>
  <skill name="web-search" path="skills/web-search/SKILL.md">
    <description>使用 Tavily 联网搜索最新信息、官方文档、新闻动态、实时行情和外部事实来源。适用于用户明确要求搜索、联网、查官网、给链接、核验事实，或任务明显依赖实时外部信息的场景。优先调用本技能目录下的 Tavily 脚本，不要退回抓搜索结果页。</description>
  </skill>
  <skill name="wiki-ask" path="skills/wiki-ask/SKILL.md">
    <description>Wiki 增强问答 — 基于 wiki 知识库的学术问题回答。使用 BM25 + embedding 混合检索，综合多个 wiki 页面回答，带来源引用。支持 Crystallize 沉淀。</description>
  </skill>
  <skill name="wiki-lint" path="skills/wiki-lint/SKILL.md">
    <description>Wiki 健康检查 — 检测结构问题（缺失 frontmatter、悬空引用、孤立页面等），支持自动修复和实体页面回填。</description>
  </skill>
</skills>
