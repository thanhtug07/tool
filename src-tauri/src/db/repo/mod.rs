//! SQL repositories (`MASTER_PLAN.md` §22 layout: `db/repo/*.rs`).
//!
//! Each repo maps one table (or logical group) to typed rows. Repos are
//! stateless and take a `&Connection`, so they compose with both direct calls
//! and the `Database::transaction` helper.

pub mod characters;
pub mod glossary;
pub mod job;
pub mod project;
pub mod subtitle;
