import { describe, expect, it } from "vitest";

import { buildListQuery } from "./list-query";

const base = { search: "", status: "" as const, showArchived: false, page: 1 };

describe("buildListQuery", () => {
  it("hides archived items by default and omits empty filters", () => {
    expect(buildListQuery(base)).toEqual({ archived: "false" });
  });

  it("shows everything with the toggle on", () => {
    expect(buildListQuery({ ...base, showArchived: true })).toEqual({
      archived: "all",
    });
  });

  it("an archived status filter implies showing archived items", () => {
    expect(buildListQuery({ ...base, status: "archived" })).toEqual({
      archived: "all",
      status: "archived",
    });
  });

  it("passes trimmed search, status, ordering, page and page size", () => {
    expect(
      buildListQuery(
        { search: "  acme ", status: "active", showArchived: false, page: 3 },
        { pageSize: 10, ordering: "name" },
      ),
    ).toEqual({
      search: "acme",
      status: "active",
      archived: "false",
      ordering: "name",
      page: 3,
      page_size: 10,
    });
  });
});
