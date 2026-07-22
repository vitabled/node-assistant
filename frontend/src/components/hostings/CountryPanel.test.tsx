import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { CountryPanel, hostingsInCountry, countryName } from "./CountryPanel";
import type { Hosting } from "./api";

const mk = (id: string, name: string, ccs: string[], tariffs: any[] = []): Hosting => ({
  id, name, website: "", notes: "", features: "",
  tariffs,
  locations: ccs.map(cc => ({ city: `${cc}-city`, country_code: cc, lat: 0, lng: 0, note: "" })),
  created_at: 0,
});

const DE = mk("h1", "Hetzner", ["DE", "FI"], [
  { name: "CX22", specs: "2 vCPU", bandwidth: "1 Гбит/с", price: 5.5, currency: "EUR", period: "mo" },
]);
const NL = mk("h2", "TransIP", ["NL"]);

describe("hostingsInCountry", () => {
  it("keeps only the locations of that country", () => {
    const rows = hostingsInCountry([DE, NL], "DE");
    expect(rows).toHaveLength(1);
    expect(rows[0].hosting.name).toBe("Hetzner");
    expect(rows[0].locations.map(l => l.country_code)).toEqual(["DE"]);
  });

  it("is case-insensitive on the code", () => {
    expect(hostingsInCountry([NL], "nl")).toHaveLength(1);
  });

  it("returns nothing for an untracked or empty country", () => {
    expect(hostingsInCountry([DE, NL], "JP")).toEqual([]);
    expect(hostingsInCountry([DE, NL], "")).toEqual([]);
  });
});

describe("CountryPanel", () => {
  it("renders hosting, city, channel and price", () => {
    render(<CountryPanel cc="DE" hostings={[DE, NL]} onClose={() => {}} />);
    expect(screen.getByText("Hetzner")).toBeInTheDocument();
    expect(screen.getByText(/DE-city/)).toBeInTheDocument();
    expect(screen.getByText("1 Гбит/с")).toBeInTheDocument();
    expect(screen.getByText(countryName("DE"))).toBeInTheDocument();
    // the other country's hosting must not leak in
    expect(screen.queryByText("TransIP")).toBeNull();
  });

  it("renders a tariff with no channel without breaking the row", () => {
    const bare = mk("h3", "Bare", ["DE"], [{ name: "S", specs: "", price: 0, currency: "USD", period: "mo" }]);
    render(<CountryPanel cc="DE" hostings={[bare]} onClose={() => {}} />);
    expect(screen.getByText("Bare")).toBeInTheDocument();
  });

  it("renders nothing when the country has no hostings", () => {
    const { container } = render(<CountryPanel cc="JP" hostings={[DE]} onClose={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });
});
