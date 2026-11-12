import type { Metadata } from "next";
import { LegalPage } from "@/components/LegalPage";
import { brand } from "@/config/brand";

export const metadata: Metadata = { title: `Terms - ${brand.appName}` };

// Starting text that describes how the app actually works. Have it reviewed before a paid launch.
export default function TermsPage() {
  return (
    <LegalPage title="Terms" updated="12 November 2026">
      <p>By creating an account you agree to these terms.</p>

      <h2>The service</h2>
      <p>
        {brand.appName} transcribes your interview and suggests answers based on the CV and job description you
        provide. Suggestions can be incomplete or wrong: check them before you use them. You decide what you say.
      </p>

      <h2>Your responsibilities</h2>
      <ul>
        <li>Some employers and interviews do not allow outside help. Follow the rules of each interview you use this in.</li>
        <li>Only upload documents you have the right to use, and keep your sign-in details private.</li>
        <li>Do not misuse the service: no attempts to break, overload or get around its limits or security.</li>
      </ul>

      <h2>Plans and payment</h2>
      <ul>
        <li>The Free plan includes a monthly number of interview minutes. Paid plans add more and renew monthly.</li>
        <li>Payments are handled by Stripe. You can change or cancel your plan at any time under
          <strong> Dashboard &gt; Manage subscription</strong>; it stays active until the end of the paid period.</li>
      </ul>

      <h2>Availability</h2>
      <p>
        The service is provided as it is. It may occasionally be unavailable, for example during updates or when a
        provider it depends on has a problem.
      </p>

      <h2>Ending your account</h2>
      <p>
        You can delete your account at any time under <strong>Account &gt; Delete account</strong>. An account that
        misuses the service may be suspended.
      </p>

      <h2>Changes</h2>
      <p>If these terms change, the date above is updated. Continuing to use the service means you accept the new terms.</p>
    </LegalPage>
  );
}
