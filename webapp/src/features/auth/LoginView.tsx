import { FormEvent } from "react";
import css from "./LoginView.module.css";

type Props = {
  loginUser: string;
  loginPass: string;
  forgotIdentifier: string;
  resetToken: string;
  resetPassword: string;
  status: string;
  onChangeUser: (value: string) => void;
  onChangePass: (value: string) => void;
  onChangeForgotIdentifier: (value: string) => void;
  onChangeResetToken: (value: string) => void;
  onChangeResetPassword: (value: string) => void;
  onLogin: (e: FormEvent) => void;
  onForgotPassword: (e: FormEvent) => void;
  onResetPassword: (e: FormEvent) => void;
};

export function LoginView(props: Props) {
  return (
    <section className={css.card}>
      <h1 className={css.title}>Ragonometrics Web</h1>
      <p className={css.subtitle}>Login to access paper-scoped chat and structured workflows.</p>
      <form className={css.form} onSubmit={props.onLogin}>
        <label className={css.label}>
          Email or Username
          <input className={css.input} value={props.loginUser} onChange={(e) => props.onChangeUser(e.target.value)} />
        </label>
        <label className={css.label}>
          Password
          <input
            className={css.input}
            type="password"
            value={props.loginPass}
            onChange={(e) => props.onChangePass(e.target.value)}
          />
        </label>
        <button className={css.button} type="submit">
          Login
        </button>
      </form>

      <details className={css.helpPanel}>
        <summary>Forgot password?</summary>
        <form className={css.form} onSubmit={props.onForgotPassword}>
          <label className={css.label}>
            Email or Username
            <input
              className={css.input}
              value={props.forgotIdentifier}
              onChange={(e) => props.onChangeForgotIdentifier(e.target.value)}
            />
          </label>
          <button className={css.button} type="submit">
            Send Reset Link
          </button>
        </form>

        <form className={css.form} onSubmit={props.onResetPassword}>
          <label className={css.label}>
            Reset token
            <input className={css.input} value={props.resetToken} onChange={(e) => props.onChangeResetToken(e.target.value)} />
          </label>
          <label className={css.label}>
            New password
            <input
              className={css.input}
              type="password"
              value={props.resetPassword}
              onChange={(e) => props.onChangeResetPassword(e.target.value)}
            />
          </label>
          <button className={css.button} type="submit">
            Reset Password
          </button>
        </form>
      </details>

      <p>{props.status}</p>
    </section>
  );
}
