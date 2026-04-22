import type { ReactNode, HTMLAttributes } from 'react';

type Props = HTMLAttributes<HTMLDivElement> & {
  children: ReactNode;
  variant?: 'default' | 'elev' | 'glow';
  interactive?: boolean;
};

export function Card({ children, className = '', variant = 'default', interactive, ...rest }: Props) {
  const base = 'rounded-xl2 bg-ink-800 shadow-card';
  const variants = {
    default: 'border border-line',
    elev:    'border border-line bg-ink-750',
    glow:    'border border-line bg-ink-800 shadow-glow',
  };
  const hover = interactive ? 'transition-all duration-200 hover:border-line-strong hover:shadow-lift hover:-translate-y-[1px]' : '';
  return (
    <div className={`${base} ${variants[variant]} ${hover} ${className}`} {...rest}>
      {children}
    </div>
  );
}
