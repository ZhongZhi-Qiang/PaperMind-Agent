import { existsSync, readFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { execSync } from "child_process";

const __dirname = dirname(fileURLToPath(import.meta.url));
const projectRoot = join(__dirname, "..");

const run = (cmd) => {
  try {
    return execSync(cmd, { stdio: "pipe", timeout: 3000 }).toString().trim();
  } catch {
    return "";
  }
};

const checks = [];

// ── 核心文件检查 ──────────────────────────

const claudeOk = existsSync(join(projectRoot, "CLAUDE.md"));
checks.push({ name: "CLAUDE.md", ok: claudeOk, hint: claudeOk ? "" : "缺少 CLAUDE.md" });

const claudeDirOk = existsSync(join(projectRoot, ".claude"));
checks.push({ name: ".claude/ 目录", ok: claudeDirOk, hint: claudeDirOk ? "" : "缺少 .claude/ 目录" });

const settingsOk = existsSync(join(projectRoot, ".claude/settings.json"));
checks.push({ name: "settings.json", ok: settingsOk, hint: settingsOk ? "" : "缺少 settings.json，Hook 无法注册" });

// ── Hook 文件检查 ─────────────────────────

const hooks = ["pre-tool-check.mjs", "session-context.mjs", "session-review.mjs", "post-tool-check.mjs", "pre-compact.mjs"];
for (const h of hooks) {
  const ok = existsSync(join(projectRoot, ".claude/hooks", h));
  if (h === "post-tool-check.mjs") {
    checks.push({ name: `hooks/${h}`, ok, hint: ok ? "" : `${h} 缺失（可选，用于 L3 自动格式化）` });
  } else if (h === "pre-compact.mjs") {
    checks.push({ name: `hooks/${h}`, ok, hint: ok ? "" : `${h} 缺失（可选，用于长会话保护）` });
  } else {
    checks.push({ name: `hooks/${h}`, ok, hint: ok ? "" : `${h} 缺失` });
  }
}

// PostToolUse 注册检查
const settingsContent = settingsOk ? readFileSync(join(projectRoot, ".claude/settings.json"), "utf-8") : "";
const postToolUseRegistered = settingsContent.includes("PostToolUse") && !settingsContent.includes('// "PostToolUse"');
checks.push({ name: "PostToolUse 已注册", ok: postToolUseRegistered, hint: postToolUseRegistered ? "" : "未在 settings.json 中启用（可选，取消注释即可）" });

// ── LSP 配置 ──────────────────────────────

const lspOk = existsSync(join(projectRoot, ".lsp.json"));
checks.push({ name: ".lsp.json", ok: lspOk, hint: lspOk ? "" : "缺少 .lsp.json" });

// ── 项目类型检测 ──────────────────────────

const hasPackageJson = existsSync(join(projectRoot, "package.json"));
const hasPyprojectToml = existsSync(join(projectRoot, "pyproject.toml"));
const hasGoMod = existsSync(join(projectRoot, "go.mod"));
const hasCargoToml = existsSync(join(projectRoot, "Cargo.toml"));
const hasGemfile = existsSync(join(projectRoot, "Gemfile"));

const detectedLanguages = [];
if (hasPackageJson) detectedLanguages.push("Node.js/TypeScript");
if (hasPyprojectToml) detectedLanguages.push("Python");
if (hasGoMod) detectedLanguages.push("Go");
if (hasCargoToml) detectedLanguages.push("Rust");
if (hasGemfile) detectedLanguages.push("Ruby");

const langLabel = detectedLanguages.length > 0 ? detectedLanguages.join(", ") : "未检测到";
checks.push({ name: "检测项目语言", ok: detectedLanguages.length > 0, hint: `已识别: ${langLabel}` });

// ── 语言服务检查（按项目类型）────────────

if (hasPackageJson) {
  const hasTsLsp = !!run("typescript-language-server --version 2>/dev/null");
  checks.push({ name: "TypeScript LSP", ok: hasTsLsp, hint: hasTsLsp ? "" : "未安装，执行 npm install -g typescript-language-server" });
}

if (hasPyprojectToml) {
  const hasPyright = !!run("pyright-langserver --version 2>/dev/null || pyright --version 2>/dev/null");
  checks.push({ name: "Python LSP (pyright)", ok: hasPyright, hint: hasPyright ? "" : "未安装，执行 pip install pyright" });
}

if (hasGoMod) {
  const hasGopls = !!run("gopls version 2>/dev/null");
  checks.push({ name: "Go LSP (gopls)", ok: hasGopls, hint: hasGopls ? "" : "未安装，执行 go install golang.org/x/tools/gopls@latest" });
}

if (hasCargoToml) {
  const hasRustAnalyzer = !!run("rust-analyzer --version 2>/dev/null");
  checks.push({ name: "Rust LSP (rust-analyzer)", ok: hasRustAnalyzer, hint: hasRustAnalyzer ? "" : "未安装，参考 https://rust-analyzer.github.io/manual.html" });
}

// 未检测到项目类型时，默认检查 TypeScript LSP
if (detectedLanguages.length === 0) {
  const hasTsLsp = !!run("typescript-language-server --version 2>/dev/null");
  checks.push({ name: "TypeScript LSP（默认）", ok: hasTsLsp, hint: hasTsLsp ? "" : "未安装，执行 npm install -g typescript-language-server" });
}

// ── OpenSpec 检查（仅当目录存在）──────────

const hasOpenSpec = existsSync(join(projectRoot, "openspec"));
if (hasOpenSpec) {
  const hasCli = !!run("openspec --version 2>/dev/null");
  checks.push({ name: "OpenSpec CLI", ok: hasCli, hint: hasCli ? "" : "未安装，执行 npm install -g @fission-ai/openspec" });
}

// ── 状态感知 ───────────────────────────

const stateOk = existsSync(join(projectRoot, ".claude/.harness-state"));
checks.push({ name: ".harness-state", ok: stateOk, hint: stateOk ? "" : "缺少状态文件，模式/阶段感知将不生效" });

// ── Skills ──────────────────────────────

const harnessInitOk = existsSync(join(projectRoot, ".claude/skills/harness-init/SKILL.md"));
checks.push({ name: "harness-init Skill", ok: harnessInitOk, hint: harnessInitOk ? "" : "缺少初始化 Skill，用户无法一键安装" });

const harnessModeOk = existsSync(join(projectRoot, ".claude/skills/harness-mode/SKILL.md"));
checks.push({ name: "harness-mode Skill", ok: harnessModeOk, hint: harnessModeOk ? "" : "缺少模式切换 Skill" });

// ── npm 分发 ────────────────────────────

const packageJsonOk = existsSync(join(projectRoot, "package.json"));
const initScriptOk = existsSync(join(projectRoot, "scripts/init.mjs"));
checks.push({ name: "npm 分发 (package.json)", ok: packageJsonOk, hint: packageJsonOk ? "" : "缺少 package.json，无法通过 npm 安装" });
checks.push({ name: "npm init 脚本", ok: initScriptOk, hint: initScriptOk ? "" : "缺少 init.mjs，npm 分发不完整" });

// ── CLAUDE.md 内容完整性 ─────────────────

if (claudeOk) {
  const content = readFileSync(join(projectRoot, "CLAUDE.md"), "utf-8");
  checks.push({
    name: "CLAUDE.md 已初始化",
    ok: !content.includes("【待填写"),
    hint: content.includes("【待填写") ? "还有占位符未替换，首次使用请对 AI 说「帮我初始化 Harness」" : "",
  });
  checks.push({
    name: "消除信息差 规则",
    ok: content.includes("消除信息差"),
    hint: content.includes("消除信息差") ? "" : "缺少「消除信息差」章节，建议从模板同步更新",
  });
}

// ── 输出 ──────────────────────────────────

const okCount = checks.filter((c) => c.ok).length;
const criticalFails = checks.filter((c) => !c.ok && !c.name.includes("可选") && !c.name.includes("已注册")).length;

console.log(`\nHarness 健康检查: ${okCount}/${checks.length} 通过\n`);
for (const c of checks) {
  const icon = c.ok ? "✅" : "❌";
  console.log(`  ${icon} ${c.name}${c.hint ? " — " + c.hint : ""}`);
}
console.log("");

// CI 模式下，关键检查失败时退出非零
if (criticalFails > 0) {
  process.exit(1);
}
