import type { Metadata } from "next";
import { LegalPage } from "@/components/LegalPage";
import { brand } from "@/config/brand";

export const metadata: Metadata = { title: `Privacy - ${brand.appName}` };

// Starting text that describes what the app actually does. Have it reviewed before a paid launch.
export default function PrivacyPage() {
  return (
    <LegalPage title="Privacy" updated="12 November 2026">
      <p>
        This page explains what {brand.appName} stores about you, why, and how to delete it.
      </p>

      <h2>What is stored</h2>
      <ul>
        <li><strong>Your account:</strong> email address and name. A password is stored only as a one-way hash; with
          Google sign-in, your Google account ID is stored instead.</li>
        <li><strong>Documents you upload:</strong> the text of your CVs and job descriptions, and search data made from them.</li>
        <li><strong>Interview history:</strong> the transcript of each interview and the answers suggested to you.</li>
        <li><strong>Usage and plan:</strong> minutes used each month, your plan, and your subscription status.</li>
      </ul>

      <h2>What is not stored</h2>
      <ul>
        <li><strong>Audio.</strong> Interview audio is sent to the speech-to-text service to be transcribed as it
          happens, and is not kept by {brand.appName}.</li>
        <li><strong>Card details.</strong> Payments are handled by Stripe; {brand.appName} never sees your card number.</li>
      </ul>

      <h2>Why</h2>
      <p>
        Only to run the service: to transcribe the interview, find the parts of your CV that answer each question,
        show you an answer, keep your history, and apply your plan. Your data is not sold or used for advertising.
      </p>

      <h2>Services that process your data</h2>
      <ul>
        <li>Vercel (hosting), Neon (database), Upstash (short-lived security data such as sign-in limits)</li>
        <li>AssemblyAI or Deepgram (speech-to-text), Groq (writing answers), Pinecone (CV search)</li>
        <li>Stripe (payments), Google (only if you choose Google sign-in)</li>
      </ul>

      <h2>Cookies</h2>
      <p>One cookie keeps you signed in. There are no advertising or analytics cookies.</p>

      <h2>Deleting your data</h2>
      <ul>
        <li>Delete a single interview from its page in the Dashboard, and documents from the Dashboard.</li>
        <li>Delete everything with <strong>Account &gt; Delete account</strong>: your account, documents, search
          data and interview history are removed, and any subscription is cancelled.</li>
      </ul>

      <h2>Security</h2>
      <p>
        Connections are encrypted, passwords are hashed, and every request is limited to the signed-in
        account&apos;s own data.
      </p>
    </LegalPage>
  );
}
