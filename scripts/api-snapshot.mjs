/**
 * Public API snapshot generator.
 *
 * 为什么不直接读 dist/index.d.ts:src/index.ts 全是 re-export,index.d.ts 只剩
 * `export { X } from "./..."`,抓不到类型【形状】(字段改名/增删看不出)。
 * 故用 TypeScript 编译器 API(typescript 已是 devDependency,零新增依赖)枚举入口模块的
 * 导出符号,逐个渲染成【归一化、按名排序、跨机确定】的签名文本 —— 一层展开命名类型,
 * 嵌套的具名类型按名引用(它们自身也是顶层导出,各有独立条目)。
 *
 * 用法:
 *   node scripts/api-snapshot.mjs           # 刷新快照(= npm run api:update),并打印变更流程警示
 *   node scripts/api-snapshot.mjs --check    # 只比对不写,不一致 exit 1
 * 测试 tests/api/api-freeze.test.ts 直接 import { generateSnapshot } 逐字比对。
 */
import ts from 'typescript';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const ENTRY = join(ROOT, 'src', 'index.ts');
export const SNAPSHOT_PATH = join(ROOT, 'tests', 'api', 'api-surface.snapshot');

// NoTruncation 是关键(否则长类型被截成 `...`);不加 UseFullyQualifiedType(会把绝对路径写进类型名 → 跨机不确定)。
const FLAGS =
  ts.TypeFormatFlags.NoTruncation |
  ts.TypeFormatFlags.WriteArrayAsGenericType |
  ts.TypeFormatFlags.UseSingleQuotesForStringLiteralType;

let _checker = null;
let _source = null;

function build() {
  const cfg = ts.readConfigFile(join(ROOT, 'tsconfig.json'), ts.sys.readFile);
  const parsed = ts.parseJsonConfigFileContent(cfg.config, ts.sys, ROOT);
  const options = {
    ...parsed.options,
    noEmit: true,
    declaration: false,
    declarationMap: false,
    sourceMap: false,
  };
  const program = ts.createProgram([ENTRY], options);
  _checker = program.getTypeChecker();
  _source = program.getSourceFile(ENTRY);
  if (!_source) throw new Error('无法加载入口 ' + ENTRY);
}

/** 只保留公共成员:private/protected 是实现细节,不属公共 API 面,纳入会让内部重构误触快照。 */
function isPublicProp(p) {
  const decls = p.getDeclarations() ?? [];
  if (decls.length === 0) return true;
  return !decls.some(
    (d) => ts.getCombinedModifierFlags(d) & (ts.ModifierFlags.Private | ts.ModifierFlags.Protected),
  );
}

/** 渲染一个类型:对象类型一层展开(调用/构造签名 + 公共属性),其余(联合/基元/字面量)用 typeToString。 */
function renderType(type) {
  const checker = _checker;
  if (!(type.flags & ts.TypeFlags.Object)) {
    return checker.typeToString(type, _source, FLAGS);
  }
  const parts = [];
  for (const sig of type.getCallSignatures())
    parts.push(checker.signatureToString(sig, _source, FLAGS) + ';');
  for (const sig of type.getConstructSignatures())
    parts.push('new ' + checker.signatureToString(sig, _source, FLAGS) + ';');
  const propLines = checker
    .getPropertiesOfType(type)
    .filter(isPublicProp)
    .map((p) => {
      const opt = p.flags & ts.SymbolFlags.Optional ? '?' : '';
      const pt = checker.getTypeOfSymbolAtLocation(p, p.valueDeclaration ?? _source);
      return `${p.getName()}${opt}: ${checker.typeToString(pt, _source, FLAGS)};`;
    })
    .sort();
  parts.push(...propLines);
  if (parts.length === 0) return checker.typeToString(type, _source, FLAGS);
  return `{ ${parts.join(' ')} }`;
}

/** 渲染一个导出符号为单行签名。 */
function renderSymbol(sym) {
  const checker = _checker;
  const name = sym.getName();
  let s = sym;
  if (s.flags & ts.SymbolFlags.Alias) s = checker.getAliasedSymbol(s);
  const f = s.flags;
  if (f & (ts.SymbolFlags.Interface | ts.SymbolFlags.Class | ts.SymbolFlags.Enum)) {
    const kind =
      f & ts.SymbolFlags.Class ? 'class' : f & ts.SymbolFlags.Enum ? 'enum' : 'interface';
    return `${kind} ${name} ${renderType(checker.getDeclaredTypeOfSymbol(s))}`;
  }
  if (f & ts.SymbolFlags.TypeAlias) {
    return `type ${name} = ${renderType(checker.getDeclaredTypeOfSymbol(s))}`;
  }
  const t = checker.getTypeOfSymbolAtLocation(s, s.valueDeclaration ?? _source);
  const kw = f & (ts.SymbolFlags.Function | ts.SymbolFlags.Method) ? 'function' : 'const';
  return `${kw} ${name}: ${renderType(t)}`;
}

/** 归一化:去掉 import("...") 路径前缀(跨机确定);统一行尾;单尾换行。 */
function normalize(text) {
  return (
    text
      .replace(/import\((?:"[^"]*"|'[^']*')\)\./g, '')
      .split('\n')
      .map((l) => l.replace(/\s+$/, ''))
      .join('\n')
      .replace(/\n{3,}/g, '\n\n')
      .trimEnd() + '\n'
  );
}

/** 生成公共 API 快照文本(按符号名排序,确定性)。 */
export function generateSnapshot() {
  if (!_checker) build();
  const moduleSymbol = _checker.getSymbolAtLocation(_source);
  if (!moduleSymbol) throw new Error('入口不是模块(无导出符号)');
  const exports = _checker.getExportsOfModule(moduleSymbol);
  const lines = exports
    .map((sym) => {
      try {
        return renderSymbol(sym);
      } catch (e) {
        return `// [快照渲染失败] ${sym.getName()}: ${e instanceof Error ? e.message : String(e)}`;
      }
    })
    .sort();
  const header =
    '// MemoWeft public API snapshot — generated compatibility baseline.\n' +
    '// Do not edit by hand; update intentional API changes with `npm run api:update`.\n\n';
  return normalize(header + lines.join('\n'));
}

const WARN =
  '\n⚠️  Public API snapshot refreshed.\n' +
  '    Review compatibility, callers, adapters, documentation, and migration impact before committing.\n' +
  '    Keep the implementation, snapshot, API contract, and changelog in the same change.\n';

// CLI 入口
if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  const snapshot = generateSnapshot();
  if (process.argv.includes('--check')) {
    let current = '';
    try {
      current = readFileSync(SNAPSHOT_PATH, 'utf8');
    } catch {
      /* 首次无快照 */
    }
    if (current !== snapshot) {
      console.error('API 快照与当前导出面不一致。运行 `npm run api:update` 刷新(先走变更流程)。');
      process.exit(1);
    }
    console.log('API 快照一致。');
  } else {
    mkdirSync(dirname(SNAPSHOT_PATH), { recursive: true });
    writeFileSync(SNAPSHOT_PATH, snapshot, 'utf8');
    console.log(`已写入 ${SNAPSHOT_PATH}(${snapshot.split('\n').length} 行)`);
    console.log(WARN);
  }
}
