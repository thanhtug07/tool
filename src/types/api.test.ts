/**
 * Cross-language contract consistency tests (TS layer, TASK-007).
 *
 * Verifies the canonical example fixtures against the TS types in `api.ts`
 * (compile-time, via `expectTypeOf`) and against the JSON Schemas in `schemas/`
 * (runtime shape checks). Full value-level validation lives in the worker's
 * pytest suite (jsonschema + generated Pydantic models).
 */

import { describe, expect, it } from "vitest";
import { expectTypeOf } from "vitest";

import apiSchema from "../../schemas/api.schema.json";
import jobSchema from "../../schemas/job.schema.json";
import projectSchema from "../../schemas/project.schema.json";
import subtitleSchema from "../../schemas/subtitle.schema.json";
import transcriptSchema from "../../schemas/transcript.schema.json";
import translationSchema from "../../schemas/translation.schema.json";

import healthValid from "../../schemas/examples/valid/health.json";
import workerStateValid from "../../schemas/examples/valid/worker_state.json";
import errorValid from "../../schemas/examples/valid/error.json";
import transcriptValid from "../../schemas/examples/valid/transcript.json";
import translationValid from "../../schemas/examples/valid/translation.json";
import subtitleValid from "../../schemas/examples/valid/subtitle.json";
import jobValid from "../../schemas/examples/valid/job.json";
import projectValid from "../../schemas/examples/valid/project.json";

import healthInvalid from "../../schemas/examples/invalid/health.json";
import workerStateInvalid from "../../schemas/examples/invalid/worker_state.json";
import errorInvalid from "../../schemas/examples/invalid/error.json";
import transcriptInvalid from "../../schemas/examples/invalid/transcript.json";
import subtitleInvalid from "../../schemas/examples/invalid/subtitle.json";
import jobInvalid from "../../schemas/examples/invalid/job.json";
import projectInvalid from "../../schemas/examples/invalid/project.json";

import type {
  ErrorResponse,
  HealthResponse,
  Job,
  Project,
  Subtitle,
  Transcript,
  Translation,
  WorkerStateInfo,
} from "./api";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type JsonObject = Record<string, any>;

/** Widen literal types so raw JSON imports (which TS widens) can be compared. */
type Widen<T> = T extends string
  ? string
  : T extends number
    ? number
    : T extends boolean
      ? boolean
      : T extends readonly (infer U)[]
        ? Widen<U>[]
        : T extends object
          ? { [K in keyof T]: Widen<T[K]> }
          : T;

function resolvePointer(schema: JsonObject, pointer: string): JsonObject {
  if (pointer === "#") {
    return schema;
  }
  let node: JsonObject = schema;
  for (const part of pointer.replace(/^#\//, "").split("/")) {
    node = node[part] as JsonObject;
  }
  return node;
}

/** Runtime drift check: example keys ⊆ schema properties, required ⊆ keys. */
function assertConforms(payload: unknown, schema: JsonObject, pointer: string): void {
  const node = resolvePointer(schema, pointer);
  const props: string[] = Object.keys((node.properties as JsonObject) ?? {});
  const required: string[] = (node.required as string[]) ?? [];
  const record = payload as Record<string, unknown>;
  for (const key of Object.keys(record)) {
    expect(props).toContain(key);
  }
  for (const key of required) {
    expect(record).toHaveProperty(key);
  }
}

describe("shared schema contracts — TS layer", () => {
  it("health: valid example matches HealthResponse", () => {
    expectTypeOf(healthValid).toMatchTypeOf<Widen<HealthResponse>>();
    assertConforms(healthValid, apiSchema, "#/$defs/HealthResponse");
  });

  it("health: invalid example is rejected at the type level", () => {
    expectTypeOf(healthInvalid).not.toMatchTypeOf<HealthResponse>();
  });

  it("worker state: valid example matches WorkerStateInfo", () => {
    expectTypeOf(workerStateValid).toMatchTypeOf<Widen<WorkerStateInfo>>();
    assertConforms(workerStateValid, apiSchema, "#/$defs/WorkerStateInfo");
  });

  it("worker state: invalid example is rejected at the type level", () => {
    expectTypeOf(workerStateInvalid).not.toMatchTypeOf<WorkerStateInfo>();
  });

  it("error envelope: valid example matches ErrorResponse", () => {
    expectTypeOf(errorValid).toMatchTypeOf<Widen<ErrorResponse>>();
    assertConforms(errorValid, apiSchema, "#/$defs/ErrorResponse");
  });

  it("error envelope: invalid example is rejected at the type level", () => {
    expectTypeOf(errorInvalid).not.toMatchTypeOf<ErrorResponse>();
  });

  it("transcript: valid example matches Transcript", () => {
    expectTypeOf(transcriptValid).toMatchTypeOf<Widen<Transcript>>();
    assertConforms(transcriptValid, transcriptSchema, "#");
  });

  it("transcript: invalid example is rejected at the type level", () => {
    expectTypeOf(transcriptInvalid).not.toMatchTypeOf<Transcript>();
  });

  it("translation: valid example matches Translation", () => {
    expectTypeOf(translationValid).toMatchTypeOf<Widen<Translation>>();
    assertConforms(translationValid, translationSchema, "#");
  });

  it("subtitle: valid example matches Subtitle", () => {
    expectTypeOf(subtitleValid).toMatchTypeOf<Widen<Subtitle>>();
    assertConforms(subtitleValid, subtitleSchema, "#");
  });

  it("subtitle: invalid example is rejected at the type level", () => {
    expectTypeOf(subtitleInvalid).not.toMatchTypeOf<Subtitle>();
  });

  it("job: valid example matches Job", () => {
    expectTypeOf(jobValid).toMatchTypeOf<Widen<Job>>();
    assertConforms(jobValid, jobSchema, "#");
  });

  it("job: invalid example is rejected at the type level", () => {
    expectTypeOf(jobInvalid).not.toMatchTypeOf<Job>();
  });

  it("project: valid example matches Project", () => {
    expectTypeOf(projectValid).toMatchTypeOf<Widen<Project>>();
    assertConforms(projectValid, projectSchema, "#");
  });

  it("project: invalid example is rejected at the type level", () => {
    expectTypeOf(projectInvalid).not.toMatchTypeOf<Project>();
  });
});
