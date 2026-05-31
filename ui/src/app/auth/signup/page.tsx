import { redirect } from 'next/navigation';

// No-auth mode: signup is disabled, redirect to home
export default function SignupPage() {
  redirect('/');
}
