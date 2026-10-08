// @vitest-environment node
// Static guard for the React Server Components boundary (F12).
//
// 1. A module without "use client" may import only components from a
//    "use client" module. On the server every export of a client module is a
//    client reference, not its value: the root layout once imported the age-gate
//    cookie name from AgeGate.tsx, got a function stub instead of "bp_age_ok",
//    and never found the cookie, so the gate came back on every page load.
//    Shared values (cookie names, constants, helpers) belong in a module
//    without the directive.
// 2. A "use client" module must not import server-only code (next/headers,
//    server-only, lib/server/*): it would run in the browser bundle.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

const frontendRoot = fileURLToPath(new URL("..", import.meta.url));
const SKIPPED_DIRS = new Set([
  "node_modules",
  ".next",
  "e2e",
  "public",
  "scripts",
  "test-results",
  "playwright-report",
]);
const SERVER_ONLY_IMPORTS = [/^next\/headers$/, /^server-only$/, /^@\/lib\/server\//];
// A component name: PascalCase with at least one lower-case letter
// (AgeGate, MatchList), never a constant (AGE_GATE_COOKIE).
const COMPONENT_NAME = /^[A-Z](?=[A-Za-z0-9]*[a-z])[A-Za-z0-9]*$/;

function relativePath(file: string): string {
  return relative(frontendRoot, file).split("\\").join("/");
}

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (SKIPPED_DIRS.has(entry)) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...sourceFiles(full));
    } else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry) && !entry.endsWith(".d.ts")) {
      out.push(full);
    }
  }
  return out.sort();
}

function parse(file: string): ts.SourceFile {
  return ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true);
}

function isClientModule(source: ts.SourceFile): boolean {
  const first = source.statements[0];
  return (
    first !== undefined &&
    ts.isExpressionStatement(first) &&
    ts.isStringLiteral(first.expression) &&
    first.expression.text === "use client"
  );
}

/** The project file an import specifier points at, or null for a package. */
function resolveImport(fromFile: string, specifier: string): string | null {
  let base: string;
  if (specifier.startsWith("@/")) base = join(frontendRoot, specifier.slice(2));
  else if (specifier.startsWith(".")) base = resolve(dirname(fromFile), specifier);
  else return null;
  for (const candidate of [base, `${base}.ts`, `${base}.tsx`, join(base, "index.ts"), join(base, "index.tsx")]) {
    if (existsSync(candidate) && statSync(candidate).isFile()) return candidate;
  }
  return null;
}

interface ImportInfo {
  specifier: string;
  /** Value (non-type) bindings; "default" and "* as" included. */
  values: string[];
}

function imports(source: ts.SourceFile): ImportInfo[] {
  const out: ImportInfo[] = [];
  for (const statement of source.statements) {
    if (!ts.isImportDeclaration(statement) || !ts.isStringLiteral(statement.moduleSpecifier)) continue;
    const clause = statement.importClause;
    const values: string[] = [];
    if (clause && !clause.isTypeOnly) {
      if (clause.name) values.push("default");
      const bindings = clause.namedBindings;
      if (bindings && ts.isNamespaceImport(bindings)) values.push(`* as ${bindings.name.text}`);
      if (bindings && ts.isNamedImports(bindings)) {
        for (const element of bindings.elements) {
          if (!element.isTypeOnly) values.push((element.propertyName ?? element.name).text);
        }
      }
    }
    out.push({ specifier: statement.moduleSpecifier.text, values });
  }
  return out;
}

const files = sourceFiles(frontendRoot);
const parsed = new Map(files.map((file) => [file, parse(file)]));
const clientFiles = new Set(files.filter((file) => isClientModule(parsed.get(file)!)));

describe("client/server module boundary", () => {
  it("finds both kinds of module", () => {
    expect(clientFiles.size).toBeGreaterThan(20);
    expect(files.length - clientFiles.size).toBeGreaterThan(20);
  });

  it("modules without 'use client' import only components from client modules", () => {
    const violations: string[] = [];
    for (const file of files) {
      if (clientFiles.has(file)) continue;
      for (const { specifier, values } of imports(parsed.get(file)!)) {
        const target = resolveImport(file, specifier);
        if (target === null || !clientFiles.has(target)) continue;
        for (const name of values) {
          if (name !== "default" && !COMPONENT_NAME.test(name)) {
            violations.push(`${relativePath(file)} imports ${name} from client module ${relativePath(target)}`);
          }
        }
      }
    }
    expect(violations).toEqual([]);
  });

  it("client modules never import server-only code", () => {
    const violations: string[] = [];
    for (const file of clientFiles) {
      for (const { specifier } of imports(parsed.get(file)!)) {
        if (SERVER_ONLY_IMPORTS.some((pattern) => pattern.test(specifier))) {
          violations.push(`${relativePath(file)} imports ${specifier}`);
        }
      }
    }
    expect(violations).toEqual([]);
  });
});
