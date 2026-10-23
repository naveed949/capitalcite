import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    "src/api/schema.ts",
  ]),
  {
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/api/askStream.ts"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "@/api/schema",
              message: "Import schema.ts only from askStream.ts.",
            },
            {
              name: "./schema",
              message: "Import schema.ts only from askStream.ts.",
            },
          ],
          patterns: [
            {
              group: ["**/api/schema", "**/api/schema.ts", "@/api/schema"],
              message: "Import schema.ts only from askStream.ts.",
            },
          ],
        },
      ],
    },
  },
]);

export default eslintConfig;
