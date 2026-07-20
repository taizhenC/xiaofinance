import { render } from "preact";
import "./styles.css";
import { ConfirmDialog, Toasts } from "./components/overlays";
import { TopBar } from "./components/TopBar";
import { route } from "./router";
import { boot } from "./state";
import { Home } from "./views/Home";
import { StockDetailView } from "./views/StockDetail";
import { Wizard } from "./views/Wizard";

function App() {
  const r = route.value;
  return (
    <>
      <TopBar />
      {r.name === "home" ? <Home /> : null}
      {r.name === "stock" ? <StockDetailView ticker={r.ticker} /> : null}
      {r.name === "wizard" ? <Wizard /> : null}
      <Toasts />
      <ConfirmDialog />
    </>
  );
}

boot();
render(<App />, document.getElementById("app")!);
