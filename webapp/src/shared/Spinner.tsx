import css from "./Spinner.module.css";

type Props = {
  label?: string;
  small?: boolean;
};

export function Spinner(props: Props) {
  return (
    <span className={css.wrap} aria-live="polite">
      <span className={`${css.dot} ${props.small ? css.small : ""}`} aria-hidden="true" />
      <span>{props.label || "Loading..."}</span>
    </span>
  );
}
