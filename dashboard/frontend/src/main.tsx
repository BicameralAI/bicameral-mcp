import { render } from "preact";
import { AppShell } from "./AppShell";
import "./styles.css";

const root = document.getElementById("app");
if (root) {
  render(<AppShell />, root);
}
