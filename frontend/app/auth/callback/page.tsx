"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { completeCognitoLogin } from "../../../lib/cognito";

export default function CognitoCallbackPage() {
  const router = useRouter();
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    completeCognitoLogin(new URL(window.location.href))
      .then(() => {
        if (active) router.replace("/");
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : "Sign-in failed.");
        }
      });
    return () => {
      active = false;
    };
  }, [router]);

  return (
    <section className="panel" aria-live="polite">
      <div className="panel-body">
        <h1>{error ? "Unable to sign in" : "Signing you in…"}</h1>
        {error && (
          <>
            <p>{error}</p>
            <Link className="button button-primary" href="/">Return to HealTest</Link>
          </>
        )}
      </div>
    </section>
  );
}
