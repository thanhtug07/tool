import { useState } from "react";

import {
  characterDelete,
  characterList,
  characterUpsert,
  glossaryDelete,
  glossaryList,
  glossaryUpsert,
} from "@/api/dictionary";
import type { CharacterEntry, GlossaryEntry } from "@/api/dictionary";

const EMPTY_PROJECT = "00000000-0000-4000-8000-000000000000";

export default function DictionaryPage() {
  const [projectId, setProjectId] = useState(EMPTY_PROJECT);
  const [glossary, setGlossary] = useState<GlossaryEntry[]>([]);
  const [characters, setCharacters] = useState<CharacterEntry[]>([]);
  const [term, setTerm] = useState("");
  const [translation, setTranslation] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    const [g, c] = await Promise.all([glossaryList(projectId), characterList(projectId)]);
    setGlossary(g);
    setCharacters(c);
  }

  async function addGlossary() {
    setError(null);
    try {
      await glossaryUpsert(projectId, term, translation);
      setTerm("");
      setTranslation("");
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  async function removeGlossary(entry: GlossaryEntry) {
    setError(null);
    try {
      await glossaryDelete(projectId, entry.term);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  async function addCharacter() {
    setError(null);
    try {
      await characterUpsert(projectId, name, description);
      setName("");
      setDescription("");
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  async function removeCharacter(entry: CharacterEntry) {
    setError(null);
    try {
      await characterDelete(projectId, entry.name);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <section aria-labelledby="dictionary-heading">
      <h1 id="dictionary-heading" className="text-lg font-semibold">
        Dictionary
      </h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Glossary terms and character notes used to build translation context.
      </p>

      <div className="mt-3 flex items-center gap-2">
        <label htmlFor="project-id" className="text-sm">
          Project ID
        </label>
        <input
          id="project-id"
          className="rounded border border-border bg-background px-2 py-1 text-sm"
          value={projectId}
          onChange={(event) => setProjectId(event.target.value)}
        />
        <button
          type="button"
          className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground"
          onClick={refresh}
        >
          Load
        </button>
      </div>

      {error && <p className="mt-2 text-sm text-destructive">{error}</p>}

      <div className="mt-6 grid grid-cols-2 gap-6">
        <section aria-labelledby="glossary-heading">
          <h2 id="glossary-heading" className="text-sm font-medium">
            Glossary
          </h2>
          <div className="mt-2 flex gap-2">
            <input
              aria-label="Term"
              className="rounded border border-border bg-background px-2 py-1 text-sm"
              placeholder="term"
              value={term}
              onChange={(event) => setTerm(event.target.value)}
            />
            <input
              aria-label="Translation"
              className="rounded border border-border bg-background px-2 py-1 text-sm"
              placeholder="translation"
              value={translation}
              onChange={(event) => setTranslation(event.target.value)}
            />
            <button
              type="button"
              className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground"
              onClick={addGlossary}
            >
              Add
            </button>
          </div>
          <ul className="mt-3 space-y-1">
            {glossary.map((entry) => (
              <li key={entry.term} className="flex items-center justify-between text-sm">
                <span>
                  <span className="font-medium">{entry.term}</span> = {entry.translation}
                </span>
                <button
                  type="button"
                  onClick={() => removeGlossary(entry)}
                  className="text-xs text-muted-foreground hover:text-destructive"
                >
                  remove
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="characters-heading">
          <h2 id="characters-heading" className="text-sm font-medium">
            Characters
          </h2>
          <div className="mt-2 flex gap-2">
            <input
              aria-label="Name"
              className="rounded border border-border bg-background px-2 py-1 text-sm"
              placeholder="name"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            <input
              aria-label="Description"
              className="rounded border border-border bg-background px-2 py-1 text-sm"
              placeholder="description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
            <button
              type="button"
              className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground"
              onClick={addCharacter}
            >
              Add
            </button>
          </div>
          <ul className="mt-3 space-y-1">
            {characters.map((entry) => (
              <li key={entry.name} className="flex items-center justify-between text-sm">
                <span>
                  <span className="font-medium">{entry.name}</span> —{" "}
                  {entry.description || "no description"}
                </span>
                <button
                  type="button"
                  onClick={() => removeCharacter(entry)}
                  className="text-xs text-muted-foreground hover:text-destructive"
                >
                  remove
                </button>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </section>
  );
}
