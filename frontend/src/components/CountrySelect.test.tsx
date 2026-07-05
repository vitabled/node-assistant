import { render, screen, cleanup, fireEvent, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CountrySelect, COUNTRIES } from "./CountrySelect";

afterEach(cleanup);

describe("CountrySelect", () => {
  it("exports a country list with the XX/unknown sentinel first", () => {
    expect(COUNTRIES[0].code).toBe("XX");
    expect(COUNTRIES.some(c => c.code === "DE")).toBe(true);
  });

  it("shows the selected country name + code with a flag chip", () => {
    render(<CountrySelect label="Страна" value="DE" onChange={() => {}} />);
    expect(screen.getByText("Germany")).toBeInTheDocument();
    expect(screen.getByText("(DE)")).toBeInTheDocument();
    // flag-icons SVG chip, not emoji text
    expect(document.querySelector(".fi.fi-de")).toBeTruthy();
  });

  it("filters the dropdown by search query (name or code)", () => {
    render(<CountrySelect label="Страна" value="" onChange={() => {}} />);
    fireEvent.click(screen.getByText("— выберите страну —"));
    const search = screen.getByPlaceholderText("Поиск страны...");
    fireEvent.change(search, { target: { value: "german" } });
    expect(screen.getByText("Germany")).toBeInTheDocument();
    expect(screen.queryByText("France")).toBeNull();
    // code search
    fireEvent.change(search, { target: { value: "fr" } });
    expect(screen.getByText("France")).toBeInTheDocument();
  });

  it("shows an empty state when nothing matches", () => {
    render(<CountrySelect label="Страна" value="" onChange={() => {}} />);
    fireEvent.click(screen.getByText("— выберите страну —"));
    fireEvent.change(screen.getByPlaceholderText("Поиск страны..."), { target: { value: "zzzzz" } });
    expect(screen.getByText("Ничего не найдено")).toBeInTheDocument();
  });

  it("calls onChange with the picked code and renders the Globe fallback for XX", () => {
    const onChange = vi.fn();
    render(<CountrySelect label="Страна" value="XX" onChange={onChange} />);
    // XX → Globe fallback, no fi-xx class
    expect(document.querySelector(".fi.fi-xx")).toBeNull();
    fireEvent.click(screen.getByText("Неизвестно"));
    const opt = screen.getAllByText("Germany")[0];
    fireEvent.click(opt);
    expect(onChange).toHaveBeenCalledWith("DE");
  });
});
