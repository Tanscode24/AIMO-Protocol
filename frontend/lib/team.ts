// Project team and startup links (shown on the About page and the sign-in page).
// photo: file in /public; focus: CSS object-position so the face is centred in the round crop.
export const BUILDERS = [
  { name: "Tanishka Singh", role: "Builder", linkedin: "https://www.linkedin.com/in/tanishka-2five/", photo: "/team/tanishka-singh.jpg", focus: "50% 45%" },
  { name: "Tarush Jain", role: "Builder", linkedin: "https://www.linkedin.com/in/tarushjain18/", photo: "/team/tarush-jain.jpg", focus: "50% 45%" },
];

export const STARTUP = { label: "Our startup", url: "https://jayash-nine.vercel.app/" };

export const host = (url: string) => url.replace(/^https?:\/\//, "").replace(/\/$/, "");
