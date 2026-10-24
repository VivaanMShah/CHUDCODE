// Contact points shown in the app. Keep these in one place.

// Appalachian Power (AEP) Virginia outage and downed-line line, 24 hours.
// Source: https://www.appalachianpower.com/contact/
export const UTILITY_PHONE_DISPLAY = "1-800-956-4237";
export const UTILITY_PHONE_TEL = "tel:18009564237";

export const mapsHospitals = (zip: string) =>
  `https://www.google.com/maps/search/hospitals+near+${encodeURIComponent(zip)}`;
export const SHELTER_FINDER =
  "https://www.redcross.org/get-help/disaster-relief-and-recovery-services/find-an-open-shelter.html";
export const nwsForecast = (zip: string) =>
  `https://forecast.weather.gov/zipcity.php?inputstring=${encodeURIComponent(zip)}`;
