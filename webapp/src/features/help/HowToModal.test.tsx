import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HowToModal } from "./HowToModal";

describe("HowToModal", () => {
  it("renders quick start and tab guide content when open", () => {
    render(<HowToModal open onClose={() => undefined} />);
    expect(screen.getByRole("dialog", { name: "How To Use Ragonometrics Web" })).toBeInTheDocument();
    expect(screen.getByText("Quick Start")).toBeInTheDocument();
    expect(screen.getByText("Tab Guide")).toBeInTheDocument();
    expect(screen.getByText("Tips")).toBeInTheDocument();
  });

  it("calls onClose for close button, overlay click, and escape", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<HowToModal open onClose={onClose} />);

    await user.click(screen.getByRole("button", { name: "Close how to popup" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    onClose.mockClear();

    await user.click(screen.getByTestId("howto-overlay"));
    expect(onClose).toHaveBeenCalledTimes(1);
    onClose.mockClear();

    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
