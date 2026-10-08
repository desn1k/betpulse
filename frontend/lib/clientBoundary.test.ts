// @vitest-environment node
// Static guard for the React Server Components boundary (F12).
//
// 1. A module without "use client" may take only components from a
//    "use client" module (import or re-export). On the server every export of a
//    client module is a client reference, not its value: the root layout once
//    imported the age-gate cookie name from AgeGate.tsx, got a function stub
//    instead of "bp_age_ok", and never found the cookie, so the gate came back on
//    every page load. Shared values (cookie names, constants, helpers) belong in
//    a module without the directive.
// 2. A "use client" module must not import server-only code (next/headers,
//    server-only, lib/server/*): it would run in the browser bundle.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

const frontendRoot = fileURLToPath(new URL("..", import.meta.url));
const serverLibRoot = join(frontendRoot, "lib", "server");
const SKIPPED_DIRS = new Set([
  "node_modules",
  ".next",
  "e2e",
  "public",
  "scripts",
  "test-results",
  "playwright-report",
]);
const SERVER_ONLY_PACKAGES = new Set(["next/headers", "server-only"]);
// A component name: PascalCase with at least one lower-case letter
// (AgeGate, MatchList), never a constant (AGE_GATE_COOKIE).
const COMPONENT_NAME = /^[A-Z](?=[A-Za-z0-9]*[a-z])[A-Za-z0-9]*$/;

type Resolver = (fromFile: string, specifier: string) => string | null;

interface ModuleEdge {
  specifier: string;
  /** Erased by TypeScript (`import type`, `export type … from`). */
  typeOnly: boolean;
  /** Value bindings taken from the target: export names, "default", or "*". */
  names: string[];
}

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

function parse(file: string, text: string): ts.SourceFile {
  return ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true);
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
const resolveOnDisk: Resolver = (fromFile, specifier) => {
  let base: string;
  if (specifier.startsWith("@/")) base = join(frontendRoot, specifier.slice(2));
  else if (specifier.startsWith(".")) base = resolve(dirname(fromFile), specifier);
  else return null;
  for (const candidate of [
    base,
    `${base}.ts`,
    `${base}.tsx`,
    join(base, "index.ts"),
    join(base, "index.tsx"),
  ]) {
    if (existsSync(candidate) && statSync(candidate).isFile()) return candidate;
  }
  return null;
};

/** Every module edge: imports and `export … from` re-exports. */
function moduleEdges(source: ts.SourceFile): ModuleEdge[] {
  const out: ModuleEdge[] = [];
  for (const statement of source.statements) {
    if (ts.isImportDeclaration(statement) && ts.isStringLiteral(statement.moduleSpecifier)) {
      const clause = statement.importClause;
      const names: string[] = [];
      if (clause && !clause.isTypeOnly) {
        if (clause.name) names.push("default");
        const bindings = clause.namedBindings;
        if (bindings && ts.isNamespaceImport(bindings)) names.push("*");
        if (bindings && ts.isNamedImports(bindings)) {
          for (const element of bindings.elements) {
            if (!element.isTypeOnly) names.push((element.propertyName ?? element.name).text);
          }
        }
      }
      // A bare `import "x"` has no clause: a side-effect import, never erased.
      out.push({
        specifier: statement.moduleSpecifier.text,
        typeOnly: clause?.isTypeOnly ?? false,
        names,
      });
    } else if (
      ts.isExportDeclaration(statement) &&
      statement.moduleSpecifier &&
      ts.isStringLiteral(statement.moduleSpecifier)
    ) {
      const names: string[] = [];
      const clause = statement.exportClause;
      if (!statement.isTypeOnly) {
        if (!clause || ts.isNamespaceExport(clause)) names.push("*");
        else
          for (const element of clause.elements) {
            if (!element.isTypeOnly) names.push((element.propertyName ?? element.name).text);
          }
      }
      out.push({
        specifier: statement.moduleSpecifier.text,
        typeOnly: statement.isTypeOnly,
        names,
      });
    }
  }
  return out;
}

function hasExportModifier(node: ts.Node): boolean {
  return ts.canHaveModifiers(node)
    ? (ts.getModifiers(node) ?? []).some((m) => m.kind === ts.SyntaxKind.ExportKeyword)
    : false;
}

function isFunctionLike(initializer: ts.Expression | undefined): boolean {
  if (!initializer) return false;
  // memo(...), forwardRef(...), dynamic(...) wrap a component.
  return (
    ts.isArrowFunction(initializer) ||
    ts.isFunctionExpression(initializer) ||
    ts.isCallExpression(initializer)
  );
}

/** Export names of a module whose value is a function (a component candidate). */
function functionExports(source: ts.SourceFile): Set<string> {
  const localFunctions = new Set<string>();
  const out = new Set<string>();
  for (const statement of source.statements) {
    if (ts.isFunctionDeclaration(statement) && statement.name) {
      localFunctions.add(statement.name.text);
      if (hasExportModifier(statement)) {
        const isDefault = (ts.getModifiers(statement) ?? []).some(
          (m) => m.kind === ts.SyntaxKind.DefaultKeyword,
        );
        out.add(isDefault ? "default" : statement.name.text);
      }
    } else if (ts.isFunctionDeclaration(statement) && hasExportModifier(statement)) {
      out.add("default"); // export default function () {}
    } else if (ts.isVariableStatement(statement)) {
      for (const declaration of statement.declarationList.declarations) {
        if (ts.isIdentifier(declaration.name) && isFunctionLike(declaration.initializer)) {
          localFunctions.add(declaration.name.text);
          if (hasExportModifier(statement)) out.add(declaration.name.text);
        }
      }
    }
  }
  for (const statement of source.statements) {
    if (ts.isExportAssignment(statement) && !statement.isExportEquals) {
      const expression = statement.expression;
      if (
        (ts.isIdentifier(expression) && localFunctions.has(expression.text)) ||
        isFunctionLike(expression)
      ) {
        out.add("default");
      }
    } else if (ts.isExportDeclaration(statement) && !statement.moduleSpecifier) {
      const clause = statement.exportClause;
      if (clause && ts.isNamedExports(clause)) {
        for (const element of clause.elements) {
          const local = (element.propertyName ?? element.name).text;
          if (localFunctions.has(local)) out.add(element.name.text);
        }
      }
    }
  }
  return out;
}

/** Rule 1: what a module without "use client" takes from client modules. */
function serverSideViolations(
  sources: Map<string, ts.SourceFile>,
  resolveImport: Resolver,
): string[] {
  const clientFiles = new Set([...sources].filter(([, s]) => isClientModule(s)).map(([f]) => f));
  const violations: string[] = [];
  for (const [file, source] of sources) {
    if (clientFiles.has(file)) continue;
    for (const { specifier, typeOnly, names } of moduleEdges(source)) {
      const target = resolveImport(file, specifier);
      if (typeOnly || target === null || !clientFiles.has(target)) continue;
      const components = functionExports(sources.get(target)!);
      for (const name of names) {
        const ok =
          name === "default"
            ? components.has("default")
            : COMPONENT_NAME.test(name) && components.has(name);
        if (!ok)
          violations.push(
            `${relativePath(file)} takes ${name} from client module ${relativePath(target)}`,
          );
      }
    }
  }
  return violations;
}

/** Rule 2: server-only code imported by a client module. */
function clientSideViolations(
  sources: Map<string, ts.SourceFile>,
  resolveImport: Resolver,
): string[] {
  const violations: string[] = [];
  for (const [file, source] of sources) {
    if (!isClientModule(source)) continue;
    for (const { specifier, typeOnly } of moduleEdges(source)) {
      if (typeOnly) continue;
      const target = resolveImport(file, specifier);
      const inServerLib = target !== null && target.startsWith(serverLibRoot + sep);
      if (SERVER_ONLY_PACKAGES.has(specifier) || inServerLib) {
        violations.push(`${relativePath(file)} imports ${specifier}`);
      }
    }
  }
  return violations;
}

describe("client/server module boundary", () => {
  const files = sourceFiles(frontendRoot);
  const sources = new Map(files.map((file) => [file, parse(file, readFileSync(file, "utf8"))]));

  it("finds both kinds of module", () => {
    const clients = [...sources.values()].filter(isClientModule).length;
    expect(clients).toBeGreaterThan(20);
    expect(files.length - clients).toBeGreaterThan(20);
  });

  it("modules without 'use client' take only components from client modules", () => {
    expect(serverSideViolations(sources, resolveOnDisk)).toEqual([]);
  });

  it("client modules never import server-only code", () => {
    expect(clientSideViolations(sources, resolveOnDisk)).toEqual([]);
  });
});

describe("the boundary rules themselves", () => {
  // In-memory modules under the real root, so relative paths behave as on disk.
  function project(modules: Record<string, string>): {
    sources: Map<string, ts.SourceFile>;
    resolver: Resolver;
  } {
    const sources = new Map(
      Object.entries(modules).map(([path, text]) => {
        const file = join(frontendRoot, path);
        return [file, parse(file, text)] as const;
      }),
    );
    const resolver: Resolver = (fromFile, specifier) => {
      let base: string;
      if (specifier.startsWith("@/")) base = join(frontendRoot, specifier.slice(2));
      else if (specifier.startsWith(".")) base = resolve(dirname(fromFile), specifier);
      else return null;
      return [`${base}.ts`, `${base}.tsx`].find((candidate) => sources.has(candidate)) ?? null;
    };
    return { sources, resolver };
  }

  const CLIENT = `"use client";
export const COOKIE = "c";
export const Height = "h-1";
export function Gate() { return null; }
const Memo = memo(Gate);
export { Memo };
export default function Page() { return null; }
`;

  it.each([
    ["an upper-case constant", 'import { COOKIE } from "@/x/client";', "COOKIE"],
    ["a PascalCase constant", 'import { Height } from "@/x/client";', "Height"],
    ["a namespace import", 'import * as all from "@/x/client";', "*"],
    ["a named re-export", 'export { COOKIE } from "@/x/client";', "COOKIE"],
    ["a star re-export", 'export * from "@/x/client";', "*"],
  ])("flags %s taken by a server module", (_label, line, name) => {
    const { sources, resolver } = project({
      "x/client.tsx": CLIENT,
      "x/server.tsx": line,
    });
    expect(serverSideViolations(sources, resolver)).toEqual([
      `x/server.tsx takes ${name} from client module x/client.tsx`,
    ]);
  });

  it("allows components, the default component and type-only imports", () => {
    const { sources, resolver } = project({
      "x/client.tsx": CLIENT,
      "x/server.tsx": [
        'import Page, { Gate, Memo } from "@/x/client";',
        'import type { COOKIE } from "@/x/client";',
        'export type { Height } from "@/x/client";',
      ].join("\n"),
    });
    expect(serverSideViolations(sources, resolver)).toEqual([]);
  });

  it("flags server-only code in a client module, by package or by resolved path", () => {
    const { sources, resolver } = project({
      "lib/server/secret.ts": "export const s = 1;",
      "x/a.tsx": '"use client";\nimport { cookies } from "next/headers";',
      "x/b.tsx": '"use client";\nimport "server-only";',
      "x/c.tsx": '"use client";\nimport { s } from "../lib/server/secret";',
      "x/d.tsx": '"use client";\nimport type { Thing } from "next/headers";',
    });
    expect(clientSideViolations(sources, resolver)).toEqual([
      "x/a.tsx imports next/headers",
      "x/b.tsx imports server-only",
      "x/c.tsx imports ../lib/server/secret",
    ]);
  });
});
