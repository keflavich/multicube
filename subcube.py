import numpy as np
import matplotlib.pylab as plt
import astropy.units as u
from astropy import log
import pyspeckit

# TODO: make informative log.info messages in
#       the methods of the SubCube class

# TODO: redefine SpectralCube.py#L795-L823 to
#       include the option of choosing between
#       the guesses in the middle of fiteach run

class SubCube(pyspeckit.Cube):
    """
    An extension of Cube, tinkered to be an instance of MultiCube, from which 
    it receives references to instances of pyspeckit.Cube that do not depend 
    on a spectral model chosen (so that parent MultiCube doesn't weigh so much)

    Is designed to have methods that operate within a single spectral model.
    """
    def __init__(self, *args, **kwargs):
        super(SubCube, self).__init__(*args, **kwargs)

        # because that UnitConversionError pops up way too often
        if self.xarr.velocity_convention is None:
            self.xarr.velocity_convention = 'radio'
        
        # so I either define some things as `None` 
        # or I'll have to call hasattr or them...
        # TODO: which is a more Pythonic approach?
        # A: probably the hasattr method, see here:  
        # http://programmers.stackexchange.com/questions/
        # 254576/is-it-a-good-practice-to-declare-instance
        # -variables-as-none-in-a-class-in-python
        self.guess_grid = None
        self.model_grid = None

    def info(self):
        def getinfo(target):
            try:
                return getattr(getattr(self,target), 'shape')
            except AttributeError:
                return 'N/A'
                pass
        log.info("Shapes of the arrays:\n"
                 "--> Data cube:\t{}\n".format(getinfo('cube'))+\
                 "--> Guess grid:\t{}\n".format(getinfo('guess_grid'))+\
                 "--> Model grid:\t{}\n".format(getinfo('model_grid'))+\
                 "--> Result rms:\t{}\n".format(getinfo('_residual_rms'))+\
                 "--> SNR map:\t{}\n".format(getinfo('snr_map')))

    def update_model(self, fit_type='gaussian'):
        """
        Tie a model to a SubCube. Didn't test it
        on anything but gaussian fitter so far.
        Yeap, I don't understand how models work.
        """
        try:
            allowed_fitters = self.specfit.Registry.multifitters
            self.specfit.fitter = allowed_fitters[fit_type]
        except KeyError:
            # TODO: get other models through add_fitter Registry method!
            raise ValueError('Unsupported fit type: %s\n'
                             'Choose one from %s' 
                             % (fit_type, allowed_fitters.keys()))
        self.specfit.fittype = fit_type
        self.fittype = fit_type

    def make_guess_grid(self, minpars, maxpars, finesse, 
            fixed=None, limitedmin=None, limitedmax=None):
        """
        Given parameter ranges and a finesse parameter, generate a grid of 
        guesses in a parameter space to be iterated upon in self.best_guess
        Maybe if parlimits arg is None we can look into parinfo?

        Parameters
        ----------
        minpars : an iterable containing minimal parameter values

        maxpars : an iterable containing maximal parameter values

        finesse : an integer or 1xNpars list/array setting the size 
                  of cells between minimal and maximal values in 
                  the resulting guess grid

        fixed : an iterable of booleans setting whether or not to fix the
                fitting parameters. Will be passed to Cube.fiteach, defaults
                to an array of False-s.

        limitedmin : an iterable of booleans controlling if the fit fixed
                     the minimal boundary of from minpars.

        limitedmax : an iterable of booleans controlling if the fit fixed
                     the maximal boundary of from maxpars.

        Returns
        -------
        guess_grid : a grid of guesses to use for SubCube.generate_model

        In addition, it saves a number of variables under self as a dictionary
        passed later to Cube.fiteach as additional arguments, with keywords:
        ['fixed', 'limitedmin', 'limitedmax', 'minpars', 'maxpars']
        """
        minpars, maxpars = np.asarray([minpars, maxpars])
        truths, falses = np.ones(minpars.shape, dtype=bool), \
                         np.zeros(minpars.shape, dtype=bool)
        
        fixed = falses if fixed is None else fixed
        limitedmin = truths if limitedmin is None else limitedmin
        limitedmax = truths if limitedmax is None else limitedmax
        # TODO: why does 'fixed' break the gaussian fitter?
        self.fiteach_args = {#'fixed'     : fixed,
                             'limitedmin': limitedmin,
                             'limitedmax': limitedmax,
                             'minpars'   : minpars,
                             'maxpars'   : maxpars    }
 
        # TODO: make sure you return the same shape!
        input_shape = minpars.shape
        npars = minpars.size

        # conformity for finesse: int or np.array goes in and np.array goes out
        finesse = np.atleast_1d(finesse) * np.ones(npars)
            
        par_space = []
        for i_len, i_min, i_max in zip(finesse, minpars, maxpars):
            par_space.append(np.linspace(i_min, i_max, i_len))

        nguesses = np.prod(map(len,par_space))
        guess_grid = np.array(np.meshgrid(*par_space)).reshape(npars, nguesses).T
        
        # this flips all the dimensions, might have some troubles should 
        # I want to scale this up later for a multidimensional case
        self.guess_grid = guess_grid

        return guess_grid

    # TODO: make a progress bar
    def _approx_modelling_time(self, guess_grid=None, n=100, dn=5):
        """
        Extrapolates the time it would take to finish the
        self.generate_model(guess_grid) call.

        Parameters
        ----------
        guess_grid : np.array; see generate_model docstring
                     If left empty, grabs self.guess_grid

        n : int, up to which size guess_grid is tested

        dn : step between the time measurements

        Returns
        -------
        t_expected : float64; linearly extrapolated execution length
        """
        if guess_grid is None:
            try:
                guess_grid = self.guess_grid
            except AttributeError:
                raise RuntimeError("Can't find the guess grid to use,")

        grid_bkp = self.model_grid
        run_lengths, run_sizes = [], []
        import time
        for i in range(0,n,dn):
            start = time.clock()
            self.generate_model(guess_grid = guess_grid[:i])
            end   = time.clock()
            run_sizes.append(i)
            run_lengths.append(end-start)
        num_models = np.prod(guess_grid.shape[:-1])
        line_fit = np.poly1d(np.polyfit(run_sizes, run_lengths, 1))
        t_expected = line_fit(num_models)

        # from here: http://stackoverflow.com/questions/6574329/
        #            how-can-i-produce-a-human-readable-difference-
        #            when-subtracting-two-unix-timestam
        from dateutil.relativedelta import relativedelta
        attrs = ['years', 'months', 'days', 'hours', 'minutes', 'seconds']
        human_readable = lambda delta: ['%d %s' % (getattr(delta, attr),
                            getattr(delta, attr) > 1 and attr or attr[:-1])
                            for attr in attrs if getattr(delta, attr)]
        readable_time = human_readable(relativedelta(seconds=t_expected))

        log.info("Estimated time to run generate_model: %s" %
                    ' '.join(readable_time))
        self.model_grid = grid_bkp

    def generate_model(self, guess_grid=None):
        """
        Generates a grid of spectral models matching the
        shape of the input guess_grid array. Can take the
        following numpy arrays as an input:

        Parameters
        ----------
        guess_grid : numpy.array
                     A grid of input parameters. 
                     Can be one of the following:
                     1) An (M,)-shaped 1d array of model parameters
                        (see pyspeckit docs for details)
                     2) An (N, M) array to compute N models 
                        for M sets of parameters
                     3) A guess cube of (Y, X, M) size
                     4) An (N, Y, X, M)-shaped array, to 
                        iterate over cubes of guesses.
                     If not set, SubCube.guess_grid is used.

        Returns
        -------
        model_grid : a grid of spectral models, following the 
                     shape of guess_grid. Also saved as an 
                     instance under SubCube.model_grid
        """
        if guess_grid is None:
            try:
                guess_grid = self.guess_grid
            except AttributeError:
                raise RuntimeError("Can't find the guess grid to use,")

        # safeguards preventing wrong output shapes
        npars = self.specfit.fitter.npars
        guess_grid = np.atleast_2d(guess_grid)
        grid_shape = guess_grid.shape[:-1]
        if ((len(grid_shape)>1 and grid_shape[-2:]!=self.cube.shape[1:]) or
            (len(grid_shape)>3) or (guess_grid.shape[-1]%npars)):
            raise ValueError("Invalid shape for the guess_grid, "
                             "check the docsting for details.")

        model_grid = np.empty(shape=grid_shape+tuple([self.xarr.size]))
        # NOTE: this for loop is the performance bottleneck!
        # would be nice if I could broadcast grid_grid to n_modelfunc...
        for idx in np.ndindex(grid_shape):
            model_grid[idx] = \
                self.specfit.get_full_model(pars=guess_grid[idx])

        self.model_grid = model_grid
        return model_grid

    def best_guess(self, model_grid=None, sn_cut=None):
        """
        For a grid of initial guesses, determine the optimal one based 
        on the preliminary residual of the specified spectral model.

        Parameters
        ----------
        model_grid : numpy.array
                     A model grid to choose from.

        use_cube : boolean
                   If true, every xy-slice of a cube will be
                   compared to every model from the model_grid.
                   sn_cut (see below) is still applied.

        sn_cut : float
                 Ignore items on xy_list if the corresponding
                 spectra have too low signal-to-noise ratios.

        Output
        ------
        best_guesses : a cube of best models corresponding to xy-grid
                       (saved as a SubCube attribute)

        best_guess : a most commonly found best guess

        best_snr_guess : the model for the least residual at peak SNR
                         (saved as a SubCube attribute)

        """
        if model_grid is None:
            if self.model_grid is None:
                raise TypeError('sooo the model_grid is empty, '
                                'did you run generate_model()?')
            model_grid = self.model_grid

        # TODO: allow for all the possible outputs from generate_model()
        if model_grid.shape[-1]!=self.cube.shape[0]:
            raise ValueError("Invalid shape for the guess_grid, "
                             "check the docsting for details.")
        if len(model_grid.shape)>2:
            raise NotImplementedError("Complex model girds aren't supported.")

        #resid_rms = lambda xy: (xy[0]-xy[1]).std(axis=0)
        # FIXME: due to broadcasting this can cause memory
        # overflows for large number of models or big cubes
        residual_rms = (self.cube[None,:,:,:]-
                            model_grid[:,:,None,None]).std(axis=1)

        if sn_cut:
            snr_mask = self.snr_map > sn_cut
            residual_rms[get_slice_mask(snr_mask)] = np.inf

        best_map   = np.argmin(residual_rms, axis=0)
        rmsmin_map = residual_rms.min(axis=0)
        self._best_map    = best_map
        self._best_rmsmap = rmsmin_map
        self.best_guesses = np.rollaxis(self.guess_grid[best_map],-1)

        from scipy.stats import mode
        model_mode = mode(best_map)
        best_model_num = model_mode[0][0,0]
        best_model_freq = model_mode[1][0,0]
        best_model_frac = (float(best_model_freq) /
                            np.prod(self.cube.shape[1:]))
        if best_model_frac < .05:
            log.warn("Selected model is best only for less than %5 "
                     "of the cube, consider using the map of guesses.")
        self._best_model = best_model_num
        self.best_guess  = self.guess_grid[best_model_num]
        log.info("Overall best model: selected #%i %s" % (best_model_num,
                 self.guess_grid[best_model_num].round(2)))

        try:
            best_snr = np.argmax(self.snr_map)
            best_snr = np.unravel_index(best_snr, self.snr_map.shape)
            self.best_snr_guess = self.guess_grid[best_map[best_snr]]
            log.info("Best model @ highest SNR: #%i %s" %
                     (best_map[best_snr], self.best_snr_guess.round(2)))
        except AttributeError:
            log.warn("Can't find the SNR map, best guess at "
                     "highest SNR pixel will not be stored.")

    def get_slice_mask(self, mask2d):
        """
        In case we ever want to apply a 2d mask to a whole cube.
        """
        mask3d = np.repeat([mask2d],self.xarr.size,axis=0)
        return mask3d

    def get_snr_map(self, signal=None, noise=None, unit='km/s', 
                    signal_mask=None, noise_mask=None          ):
        """
        Calculates S/N ratio for the cube. If no information is given on where
        to look for signal and noise channels, a (more-or-less reasonable) rule
        of thirds is used: the outer thirds of the channel range are used to 
        get the root mean square of the noise, and the max value in the inner 
        third is assumed to be the signal strength.
        
        Parameters
        ----------
        signal : 2xN numpy.array, where N is the total number of signal blocks.
                 Should contain channel numbers in `unit` convention, the first
                 subarray for start of the signal block and the second one for
                 the end of the signal block

        noise : 2xN numpy.array, where N is the total number of noise blocks.
                Same as `signal` otherwise.

        unit : a unit for specifying the channels. Defaults to 'km/s'.
               If set to 'pixel', actual channel numbers are selected.

        signal_mask : dtype=bool numpy.array of SubCube.xarr size
                      If specified, used as a mask to get channels with signal.
                      Overrules `signal`

        noise_mask : dtype=bool numpy.array of SubCube.xarr size
                     If specified, used as a mask to get channels with noise.
                     Overrules `noise`

        Returns
        -------
        snr_map : numpy.array
                  Also stored under SubCube.snr_map
        """
        # will override this later if no ranges were actually given
        unit = {'signal': unit, 'noise': unit}

        # get rule of thirds signal and noise if no ranges were given
        default_cut = 0.33
        if signal is None:
            # find signal cuts for the current unit?
            # nah let's just do it in pixels, shall we?
            i_low, i_high = int(round(self.xarr.size *    default_cut )),\
                            int(round(self.xarr.size * (1-default_cut)))
            signal = [[i_low+1], [i_high-1]]
            unit['signal'] = 'pixel'

        if noise is None:
            # find signal cuts for the current unit?
            # nah let's just do it in pixels, shall we?
            i_low, i_high = int(round(self.xarr.size *    default_cut )),\
                            int(round(self.xarr.size * (1-default_cut)))
            noise = [[0, i_high], [i_low, self.xarr.size-1]]
            unit['noise'] = 'pixel'

        # setting xarr masks from high / low indices
        if signal_mask is None:
            signal_mask = self.get_mask(*signal, unit=unit['signal'])
        if noise_mask is None:
            noise_mask = self.get_mask(*noise, unit=unit['noise'])
        self._mask_signal = signal_mask
        self._mask_noise = noise_mask

        # no need to care about units at this point
        snr_map = self.get_signal_map(signal_mask) / \
                             self.get_rms_map(noise_mask)
        self.snr_map = snr_map
        return snr_map

    def get_mask(self, low_indices, high_indices, unit):
        """
        Converts low / high indices arrays into a mask on self.xarr
        """
        mask = np.array([False]*self.xarr.size)
        for low, high in zip(low_indices, high_indices):
            # you know this is a hack right?
            # also, undocumented functionality is bad and you should feel bad
            if unit not in ['pix','pixel','pixels','chan','channel','channels']:
                # converting whatever units we're given to pixels
                unit_low, unit_high = low*u.Unit(unit), high*u.Unit(unit)
                try:
                    # FIXME: this is too slow, find a better way!
                    unit_bkp = self.xarr.unit
                    self.xarr.convert_to_unit(unit)
                except u.core.UnitConversionError as err:
                    raise type(err)(str(err) + "\nConsider setting, e.g.:\n"
                            "SubCube.xarr.velocity_convention = 'radio'\n"
                            "and\nSubCube.xarr.refX = line_freq*u.GHz")
                index_low  = self.xarr.x_to_pix(unit_low)
                index_high = self.xarr.x_to_pix(unit_high)
                self.xarr.convert_to_unit(unit_bkp)
            else: 
                try:
                    index_low, index_high = int(low.value ),\
                                            int(high.value)
                except AttributeError:
                    index_low, index_high = int(low), int(high)

            # so this also needs to be sorted if the axis goes in reverse
            index_low, index_high = np.sort([index_low, index_high])

            mask[index_low:index_high] = True

        return mask

    def get_rms_map(self, noise_mask=None):
        """
        Make an rms estimate, will try to find the noise channels in
        the input values or in class instances. If noise mask is not
        not given, defaults to calculating rms of all channels.

        Parameters
        ----------
        noise_mask : dtype=bool numpy.array of SubCube.xarr size
                     If specified, used as a mask to get channels with noise.

        Returns
        -------
        rms_map : numpy.array, also stored under SubCube.rms_map
        """
        if noise_mask is None:
            log.warn('no noise mask was given, will calculate the RMS '
                     'over all channels, thus overestimating the noise!')
            noise_mask = np.ones(self.xarr.shape, dtype=bool)
        rms_map = self.cube[noise_mask,:,:].std(axis=0)
        self._rms_map = rms_map
        return rms_map

    def get_signal_map(self, signal_mask=None):
        """
        Make a signal strength estimate. If signal mask is not
        not given, defaults to maximal signal on all channels.

        Parameters
        ----------
        signal_mask : dtype=bool numpy.array of SubCube.xarr size
                      If specified, used as a mask to get channels with signal.

        Returns
        -------
        signal_map : numpy.array, also stored under SubCube.signal_map
        """
        if signal_mask is None:
            log.warn('no signal mask was given, will calculate the signal '
                     'over all channels: true signal might be lower.')
            signal_mask = np.array(self.xarr.shape, dtype=bool)
        signal_map = self.cube[signal_mask,:,:].max(axis=0)
        self._signal_map = signal_map
        return signal_map

    def get_chi_squared(self, sigma = None, refresh=False):
        """
        Computes a chi-squared map from modelcube / parinfo.
        """
        if self._modelcube is None or refresh:
            self.get_modelcube()

        if sigma is None:
            sigma = self._rms_map

        chisq = ((self.cube - self._modelcube)**2).sum(axis=0)/sigma**2

        self.chi_squared = chisq
        return chisq

    def chi_squared_stats(self, plot_chisq=False):
        """
        Compute chi^2 statistics for an X^2 distribution.
        This is essentially a chi^2 test for normality being
        computed on residual from the fit. I'll rewrite it 
        into a chi^2 goodness of fit test when I'll get around
        to it.

        Returns
        -------
        prob_chisq : probability that X^2 obeys the chi^2 distribution

        dof : degrees of freedom for chi^2
        """
        # ------------------- TODO --------------------- #
        # rewrite it to a real chi-square goodness of fit!
        # this is essentially a chi^2 test for normality
        from scipy.stats import chisqprob

        # TODO: for Pearson's chisq test it would be
        # dof = self.xarr.size - self.specfit.fitter.npars - 1
        
        # NOTE: likelihood function should asymptotically approach
        #       chi^2 distribution too! Given that the whole point
        #       of calculating chi^2 is to use it for model 
        #       selection I should probably switch to it.

        # TODO: derive an expression for this "Astronomer's X^2" dof.
        dof = self.xarr.size
        prob_chisq = chisqprob(self.chi_squared, dof)

        if plot_chisq:
            if not plt.rcParams['text.usetex']:
                plt.rc('text', usetex=True)
            if self.mapplot.figure is None:
                self.mapplot()
            self.mapplot.plane = prob_chisq
            self.mapplot(estimator=None, cmap='viridis', vmin=0, vmax=1)
            labtxt = r'$\chi^2\mathrm{~probability~(%i~d.o.f.)}$' % dof
            self.mapplot.FITSFigure.colorbar.set_axis_label_text(labtxt)
            plt.show()

        self.prob_chisq = prob_chisq

        return prob_chisq, dof

    def mark_bad_fits(self, ax = None, mask = None, 
                      cut = 1e-20, method = 'cross', **kwargs):
        """
        Given an active axis used by Cube.mapplot, overplot 
        pixels with bad fits with an overlay.

        Can pass along a mask of bad pixels; if none is given 
        the method tries to get its own guess from:
        self.prob_chisq < cut

        Additional keyword arguments are passed to plt.plot.
        """
        # setting defaults for plotting if no essentials are passed
        ax = ax or self.mapplot.axis
        pltkwargs = {'alpha': 0.8, 'ls': '--', 'lw': 1.5, 'c': 'r'}
        pltkwargs.update(kwargs)
        # because the plotting routine would attempt to change the scale
        try:
            ax.autoscale(False)
        except AttributeError:
            raise RuntimeError("Can't find an axis to doodle on.")

        # NOTE: this would only work for a singular component
        #       due to the way we're calculating X^2. One can,
        #       in principle, calculate X^2 with a mask to
        #       bypass this issue, but only in the case of the
        #       components being clearly separated.
        #       Otherwise the cut value needs to be set "by eye"
        mask = self.prob_chisq < cut if self.prob_chisq is not None else mask

        # that +1 modifier is there because of aplpy's
        # convention on the (0,0) origin in FITS files
        for y,x in np.stack(np.where(mask)).T+1:
            self._doodle_xy(ax, (y,x), method, **pltkwargs)

    def _doodle_xy(self, ax, xy, method, **kwargs):
        """
        Draws lines on top of a pixel.

        Parameters
        ----------
        ax : axis to doodle on

        xy : a tuple of xy coordinate pair

        method : what to draw. 'box' and 'cross' are supported
        """
        x, y = xy
        if method is 'box':
            ax.plot([x-.5,x-.5,x+.5,x+.5,x-.5], 
                    [y-.5,y+.5,y+.5,y-.5,y-.5], 
                    **kwargs)
        elif method is 'cross':
            ax.plot([x-.5,x+.5], [y-.5,y+.5], **kwargs)
            ax.plot([x+.5,x-.5], [y-.5,y+.5], **kwargs)
        else:
            raise ValueError("unknown method %s passed to "
                             "the doodling function" % method)

    def get_likelihood(self, sigma = None):
        """
        Computes log-likelihood map from chi-squared
        """
        # self-NOTE: need to deal with chi^2 first
        raise NotImplementedError
    #    if sigma is None:
    #        sigma = self._rms_map

    #    # TODO: resolve extreme exponent values or risk overflowing
    #    likelihood=np.exp(-self.chi_squared/2)* \
    #           (sigma*np.sqrt(2*np.pi))**(-self.xarr.size)
    #    self.likelihood = np.log(likelihood)

    #    return np.log(likelihood)
