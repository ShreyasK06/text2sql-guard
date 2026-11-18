import { useNow } from "../hooks";
import { FAIL_COPY, secondsLeft, type ConsoleState } from "../lib/consoleMachine";
import { Icon } from "./Icon";

type Failed = Extract<ConsoleState, { phase: "failed" }>;

/** A provider or network failure. It is a different thing from a guardrail refusal and looks different. */
export function FailPanel({ f, onRetry, canSql }: { f: Failed; onRetry: () => void; canSql: boolean }) {
  const copy = FAIL_COPY[f.kind];
  const waiting = f.retryAt != null;
  const now = useNow(waiting);
  const left = secondsLeft(f.retryAt, now);
  const locked = waiting && left > 0;
  return (
    <section className="verdict fail" aria-labelledby="failh">
      <div className="vhead">
        <span className="vicon" aria-hidden="true"><Icon name="alert" /></span>
        <h2 id="failh">{copy.title}</h2>
        <span className="vtag">Model or network problem, not a policy decision</span>
      </div>
      <p className="why">{copy.body}</p>
      {f.message ? <p className="why detail"><b>Detail:</b> {f.message}</p> : null}
      <p className="why"><b>What to do:</b> {copy.fix}{canSql ? " You can also tick Supply SQL directly, which skips the model." : ""}</p>
      <div className="row">
        <button type="button" className="btn" disabled={locked} onClick={onRetry}>
          {locked ? <>Run again in <span className="num" role="timer">{left}s</span></> : "Run again"}
        </button>
        {locked ? <span className="small muted">The button unlocks when the provider says it is ready.</span> : null}
      </div>
    </section>
  );
}
